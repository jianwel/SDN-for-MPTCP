#!/usr/bin/python

''' SDN Controller -- Receives router updates, constructs a stable view of the network, and makes routing changes to improve flow throughput '''

import os
import sys
import json
import socket
import struct
import threading
import time
import argparse
import logging
import shlex

file_path = os.path.dirname(sys.argv[0])
protobuf_path = os.path.abspath(os.path.join(file_path, '../protobuf'))
sys.path.append(protobuf_path)
import update_pb2

CONTROLLER_IP = ''
CONTROLLER_PORT = 36502

NODE_EXPIRE_TIME = 5.0
NETWORK_REFRESH_ITVL = 5

DEBUG_FILEPATH = '/tmp/sdncontroller.txt'


''' Network class defining a graph of nodes '''
class Network:
  def __init__(self):
    self.nodes = []
    self.nodes_dict = {}  # Alias -> Node instance
    self.nodes_lock = threading.Lock()

  def __str__(self):
    return self.debug_print()

  def update_node(self, alias, ip, report, socket):
    self.nodes_lock.acquire(True)

    if alias in self.nodes_dict:
      node = self.nodes_dict[alias]
      node.last_update = time.time()
    else:
      routes = {}
      if report.HasField('routes'):
        routes = json.loads(report.routes)

      interfaces = {}
      if report.HasField('interfaces'):
        interfaces = json.loads(report.interfaces)

      node = Node(alias, ip, self, socket, routes, interfaces)
      self.nodes.append(node)
      self.nodes_dict[alias] = node

    if len(report.neighbors) > 0:
      for neighbor in report.neighbors:
        neighbor_alias = neighbor.ip
        if neighbor.HasField('alias'):
          neighbor_alias = neighbor.alias

        if neighbor_alias in self.nodes_dict:  # neighbor must already be known by controller
          neighbor_node = self.nodes_dict[neighbor_alias]
          #TODO: Include interface name in update (needed for rerouting)
          node.update_neighbor(neighbor_node, 'unknown', neighbor.ip, float(neighbor.rtt))

    self.nodes_lock.release()

  def refresh_node_list(self):
    self.nodes_lock.acquire(True)

    if len(self.nodes) < 1:
      self.nodes_lock.release()
      return

    nodes_expired = []

    # Expire nodes that do not appear in any neighbor's list
    curtime = time.time()
    for node in self.nodes:
      if curtime - node.last_update >= NODE_EXPIRE_TIME:
        found = False
        for neighbor in node.neighbors:
          if node.socket and curtime - neighbor.last_update >= NODE_EXPIRE_TIME and neighbor.has_neighbor(node):
            found = True
            break
        if not found:
          nodes_expired.append(node)

    for node in nodes_expired:
      self.nodes.remove(node)
      del self.nodes_dict[node.alias]

    self.nodes_lock.release()

  def set_node_lost(self, alias):
    self.nodes_lock.acquire(True)
    if alias in self.nodes_dict:
      self.nodes_dict[alias].socket = None  # closed by server worker
    self.nodes_lock.release()

  def debug_print(self):
    self.nodes_lock.acquire(True)

    graph = 'Network (Size ' + str(len(self.nodes)) + ')\n'

    for node in self.nodes:
      graph += '%s -> (%d)\n' % (str(node), len(node.neighbors))
      for neighbor, link in node.neighbors.iteritems():
        graph += '  %s \t %s\t rtt=%.6f\n' % (str(neighbor), str(link), link.rtt)

    #for node in self.nodes:
    #  graph += str(node) + ' -> ' + ', '.join([str(n) for n in node.neighbors]) + '\n'

    self.nodes_lock.release()

    return graph


''' Node class representing a client in the network '''
class Node:
  def __init__(self, alias, ip, network, socket, routes, interfaces):
    self.alias = alias
    self.ip = ip  # endpoint in node <-> controller connection
    self.neighbors = {}  # Node instance -> Link instance
    self.network = network
    self.socket = socket
    self.routes = routes
    self.interfaces = interfaces
    self.last_update = time.time()

  def update_neighbor(self, neighbor_node, interface, to_ip, rtt):
    if neighbor_node in self.neighbors:
      link = self.neighbors[neighbor_node]
      link.rtt = rtt
    else:
      link = Link(interface, to_ip, rtt)
      self.neighbors[neighbor_node] = link

  def has_neighbor(self, node):
    return node in self.neighbors

  def get_link(self, node):
    if node in self.neighbors:
      return self.neighbors[node]
    return None

  def __str__(self):
    return self.alias


''' Link class defining statistics for a neighboring link '''
class Link:
  def __init__(self, interface, to_ip, rtt):
    self.interface = interface
    self.to_ip = to_ip
    self.rtt = rtt

  def __str__(self):
    return '%s via %s' % (self.to_ip, self.interface)


# http://eli.thegreenplace.net/2011/08/02/length-prefix-framing-for-protocol-buffers
def socket_read_n(sock, n):
    ''' Read exactly n bytes from the socket.
        Raise RuntimeError if the connection closed before
        n bytes were read.
    '''
    buf = ''
    while n > 0:
      data = sock.recv(n)
      if data == '':
        break
      buf += data
      n -= len(data)
    return buf

def receive_worker(conn, addr, network, done_event):
  # Handle a single client
  while not done_event.is_set():
    try:
      len_buf = socket_read_n(conn, 4)
      if len(len_buf) == 0:
        logging.debug('Lost connection from {0}'.format(addr))
        network.set_node_lost(alias)
        return
      msg_len = struct.unpack('>L', len_buf)[0]
      msg = socket_read_n(conn, msg_len)

      report = update_pb2.Report()
      report.ParseFromString(msg)

      alias = addr[0]
      if report.HasField('alias'):
        alias = report.alias

      logging.debug('Report from {0}: time = {1}'.format(alias, report.timestamp))

      network.update_node(alias, addr[0], report, conn)
      network.refresh_node_list()

    except socket.error, exc:
      logging.debug('Lost connection from {0}'.format(addr))
      return

def server_worker(done_event, network, args):
  # Initialize server for receiving updates
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.bind((args.controller_ip, args.controller_port))
  s.settimeout(5)
  s.listen(5)
  logging.debug('Listening on port {0} for incoming connections'.format(args.controller_port))

  # Create one receive worker thread for each client
  clients = []
  while not done_event.is_set():
    if len(clients) < 5:
      try:
        conn, addr = s.accept()
      except socket.timeout:
        continue
      except socket.error, exc:
        logging.debug('Accept error: {0}'.format(exc))
        s.close()
        return

      logging.debug('Got connection from {0}'.format(addr))
      t = threading.Thread(target=receive_worker, args=[conn, addr, network, done_event])
      t.start()
      clients.append((t, conn, addr))

    # Refresh client list for alive threads
    done_threads = []
    for t, conn, _ in clients:
      if not t.isAlive():
        conn.close()
        done_threads.append(t)
    clients = [c for c in clients if c[0] not in done_threads]
    time.sleep(0)  # yield

  # Refresh network
  for t, conn, _ in clients:
    conn.close()
    t.join()


def network_refresh_worker(done_event, network):
  while not done_event.is_set():
    time.sleep(NETWORK_REFRESH_ITVL)
    network.refresh_node_list()
    logging.debug(network)


def input_worker(done_event, network):
  while not done_event.is_set():
    cmd = shlex.split(raw_input('> ').strip())
    if len(cmd) <= 0:
      continue
    elif cmd[0] == 'exit':
      done_event.set()
      break
    else:
      print 'usage:\n'


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('-c', '--controller-ip', default=CONTROLLER_IP, help='controller IP address')
  parser.add_argument('-p', '--controller-port', default=CONTROLLER_PORT, help='controller port')
  parser.add_argument('-d', '--debug-file', default=DEBUG_FILEPATH, help='debug file')
  parser.add_argument('-D', '--disable-debug', action='store_true', help='disable debug logging')
  parser.add_argument('-P', '--print-stdout', action='store_true', help='print debug info to stdout')
  args = parser.parse_args()

  if not args.disable_debug:
    logging.basicConfig(level=logging.DEBUG, filename=args.debug_file)

  if args.print_stdout:
    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.DEBUG)
    streamHandler = logging.StreamHandler(sys.stdout)
    streamHandler.setLevel(logging.DEBUG)
    rootLogger.addHandler(streamHandler)

  network = Network()

  done_event = threading.Event()
  threads = []
  t1 = threading.Thread(target=server_worker, args=[done_event, network, args])
  t1.start()
  t2 = threading.Thread(target=network_refresh_worker, args=[done_event, network])
  t2.start()
  t3 = threading.Thread(target=input_worker, args=[done_event, network])
  t3.start()
  threads.extend([t1, t2, t3])

  try:
    while not done_event.is_set():
      time.sleep(0.1)
  except KeyboardInterrupt:
    done_event.set()

  print '\nClosing...'
  for t in threads:
    t.join()

if __name__ == '__main__':
  main()
