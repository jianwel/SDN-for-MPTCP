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

file_path = os.path.dirname(sys.argv[0])
protobuf_path = os.path.abspath(os.path.join(file_path, '../protobuf'))
sys.path.append(protobuf_path)
import update_pb2

CONTROLLER_IP = ''
CONTROLLER_PORT = 36502

NODE_EXPIRE_TIME = 5.0

DEBUG_FILEPATH = "/tmp/sdncontroller.txt"


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

    node_neighbors = []
    if len(report.neighbors) > 0:
      for neighbor in report.neighbors:
        neighbor_alias = neighbor.ip
        if neighbor.HasField('alias'):
          neighbor_alias = neighbor.alias
        if neighbor_alias in self.nodes_dict:
          node_neighbors.append(self.nodes_dict[neighbor_alias])
    node.neighbors = node_neighbors

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
          if node.socket and curtime - neighbor.last_update >= NODE_EXPIRE_TIME and node.socketnode in neighbor.neighbors:
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
      graph += str(node) + ' -> ' + ', '.join([str(n) for n in node.neighbors]) + '\n'

    self.nodes_lock.release()

    return graph


''' Node class representing a client in the network '''
class Node:
  def __init__(self, alias, ip, network, socket, routes, interfaces):
    self.alias = alias
    self.ip = ip
    self.neighbors = []
    self.network = network
    self.socket = socket
    self.routes = routes
    self.interfaces = interfaces
    self.last_update = time.time()

  def __str__(self):
    return self.alias


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
      logging.debug("Lost connection from", addr)
      return

def server_worker(controller_ip, controller_port, network, done_event):
  # Initialize server for receiving updates
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.bind((controller_ip, controller_port))
  s.settimeout(5)
  s.listen(5)
  logging.debug('Listening on port {0} for incoming connections'.format(controller_port))

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


def debug_print_worker(network, done_event):
  while not done_event.is_set():
    time.sleep(5)
    network.refresh_node_list()
    logging.debug(network)


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
  t1 = threading.Thread(target=server_worker, args=[args.controller_ip, args.controller_port, network, done_event])
  t1.start()
  t2 = threading.Thread(target=debug_print_worker, args=[network, done_event])
  t2.start()
  threads.extend([t1, t2])

  try:
    while True:
      time.sleep(0.1)
  except KeyboardInterrupt:
    print 'Closing...'
    done_event.set()
    for t in threads:
      t.join()

if __name__ == '__main__':
  main()
