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
import traceback
import heapq
import re
import decimal
from decimal import Decimal as D

file_path = os.path.dirname(sys.argv[0])
protobuf_path = os.path.abspath(os.path.join(file_path, '../protobuf'))
sys.path.append(protobuf_path)
import update_pb2

CONTROLLER_IP = ''
CONTROLLER_PORT = 36502

NODE_EXPIRE_TIME = 5.0
NETWORK_REFRESH_ITVL = 3

DEBUG_FILEPATH = '/tmp/sdncontroller.txt'

''' Network class defining a graph of nodes '''
class Network:
  def __init__(self):
    self.nodes = {} # Alias -> Node instance
    self.nodes_lock = threading.Lock()
    self.c_max = 0.0

  def __str__(self):
    return self.debug_print()

  def update_node(self, alias, ip, report, socket):
    self.nodes_lock.acquire(True)

    flows = {}
    if report.HasField('flows'):
      flows = json.loads(report.flows)

    routes = {}
    if report.HasField('routes'):
      routes = json.loads(report.routes)

    interfaces = {}
    if report.HasField('interfaces'):
      interfaces = json.loads(report.interfaces)

    if alias in self.nodes:
      node = self.nodes[alias]
      node.last_update = time.time()
      node.flows = flows;
      node.routes = routes;
      node.interfaces = interfaces;
    else:
      node = Node(alias, ip, self, socket, flows, routes, interfaces)
      self.nodes[alias] = node

    if len(report.neighbors) > 0:
      for neighbor in report.neighbors:
        neighbor_alias = neighbor.ip
        if neighbor.HasField('alias'):
          neighbor_alias = neighbor.alias

        neighbor_interface = 'unknown'
        if neighbor.HasField('interface'):
          neighbor_interface = neighbor.interface

        neighbor_capacity = 1.0
        if neighbor.HasField('capacity'):
          neighbor_capacity = float(neighbor.capacity)
          if neighbor_capacity > self.c_max:
            self.c_max = neighbor_capacity

        # Neighbor must already be known by controller.
        if neighbor_alias in self.nodes:
          neighbor_node = self.nodes[neighbor_alias]
          node.update_neighbor(neighbor_node, neighbor_interface, neighbor.ip, float(neighbor.rtt), neighbor_capacity)

    self.nodes_lock.release()

  def refresh_node_list(self):
    self.nodes_lock.acquire(True)

    if len(self.nodes.keys()) < 1:
      self.nodes_lock.release()
      return

    nodes_expired = {}
    # Expire nodes that do not appear in any neighbor's list
    curtime = time.time()
    for alias, node in self.nodes.iteritems():
      if curtime - node.last_update >= NODE_EXPIRE_TIME:
        found = False
        for neighbor in node.neighbors:
          if node.socket and curtime - neighbor.last_update >= NODE_EXPIRE_TIME and neighbor.has_neighbor(node):
            found = True
            break
        if not found:
          nodes_expired[alias] = node

    for alias in nodes_expired:
      del self.nodes[alias]

    self.nodes_lock.release()

  def set_node_lost(self, alias):
    self.nodes_lock.acquire(True)
    if alias in self.nodes:
      self.nodes[alias].socket = None  # closed by server worker
    self.nodes_lock.release()

  def debug_print(self):
    class Flow:
      def __init__(self, ep_a, ep_b, bps):
        self.ep_a = ep_a
        self.ep_b = ep_b
        self.bps = bps
      def __repr__(self):
        return 'Flow({ep_a}, {ep_b}, {bps} bit/s)'.format(
          ep_a=self.ep_a,
          ep_b=self.ep_b,
          bps=self.bps)

    self.nodes_lock.acquire(True)

    ip_addrs = {} # IP address -> alias
    for node in self.nodes.itervalues():
      for name, iface in node.interfaces.iteritems():
        ip_addrs[iface['addr']] = node.alias

    graph = 'Network ({size})\n'.format(size=len(self.nodes.keys()))

    for node in self.nodes.itervalues():
      graph += '  {node}\n'.format(node=node)
      graph += '    Neighbors ({count})\n'.format(
        count=len(node.neighbors.keys()))
      for neighbor, link in node.neighbors.iteritems():
        graph += '      {neighbor}\t{link}\t{cap:.2f}\t{rtt:.6f}\n'.format(
          neighbor=neighbor,
          link=link,
          cap=link.capacity,
          rtt=link.rtt)

      if 'connections' in node.flows:
        flows = []
        for connection in node.flows['connections']:
          for field in ['src2dst', 'dst2src']:
            m = re.match(r'(.*):\d+ -> (.*):\d+', connection[field]['flow'])
            n = re.match(r'(.*) bit/s', connection[field]['linkLayerThroughput'])
            if m and n:
              ep_a = m.group(1)
              if ep_a in ip_addrs:
                ep_a = ip_addrs[ep_a]

              ep_b = m.group(2)
              if ep_b in ip_addrs:
                ep_b = ip_addrs[ep_b]

              ep_first = (ep_a if ep_a < ep_b else ep_b)
              ep_second = (ep_b if ep_a < ep_b else ep_a)
              bps = D(n.group(1))
              found = False
              for flow in flows:
                if flow.ep_a == ep_first and flow.ep_b == ep_second:
                  flow.bps += bps
                  found = True
                  break

              if not found:
                flows.append(Flow(ep_first, ep_second, bps))

        graph += '    Flows ({count})\n'.format(count=len(flows))
        for flow in flows:
          graph += '      {ep_a}\t{ep_b}\t{bps} bit/s\n'.format(
            ep_a=flow.ep_a,
            ep_b=flow.ep_b,
            bps=flow.bps)

    self.nodes_lock.release()

    return graph

  def send_from_node(self, alias, message):
    res = ''

    self.nodes_lock.acquire(True)
    if alias in self.nodes:
      try:
        self.nodes[alias].socket.send(message)
        res = 'Sent message to ' + alias + '.'
      except:
        res = 'Failed to send message to ' + alias + '.'
    else:
      res = alias + ' not found in network.'
    self.nodes_lock.release()

    return res


''' Node class representing a client in the network '''
class Node:
  def __init__(self, alias, ip, network, socket, flows, routes, interfaces):
    self.alias = alias
    self.ip = ip  # endpoint in node <-> controller connection
    self.neighbors = {}  # Node instance -> Link instance
    self.network = network
    self.socket = socket
    self.flows = flows
    self.routes = routes
    self.interfaces = interfaces
    self.last_update = time.time()

  def update_neighbor(self, neighbor_node, interface, to_ip, rtt, capacity):
    if neighbor_node in self.neighbors:
      link = self.neighbors[neighbor_node]
      link.rtt = rtt
    else:
      link = Link(interface, to_ip, rtt, capacity)
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
  def __init__(self, interface, to_ip, rtt, capacity):
    self.interface = interface
    self.to_ip = to_ip
    self.rtt = rtt
    self.capacity = capacity

  def __str__(self):
    return '%s via %s' % (self.to_ip, self.interface)


''' Returns a dictionary key for an edge as an ordered pair of nodes '''
def edge_key(node_a, node_b):
  return (node_a, node_b) if node_a < node_b else (node_b, node_a)


''' Run modified Dijkstra to find path with highest Q gain in the network '''
def find_best_path(network, Q, start, end):
  if not (start and end):
    return None

  # Initialize network
  for node in network.nodes.itervalues():
    node.visited = False
    node.previous = None
    node.hop_count = 0
    node.gain = 0
  start.gain = float('inf')

  # Create max heap of unvisited nodes
  unvisited = [(-n.gain, n) for n in network.nodes.itervalues()]
  heapq.heapify(unvisited)

  while len(unvisited) > 0:
    next_pair = heapq.heappop(unvisited)
    current = next_pair[1]
    current.visited = True

    for neighbor, link in current.neighbors.iteritems():
      if neighbor.visited:
        continue
      new_gain = min(current.gain, Q[edge_key(current, neighbor)])
      new_hop_count = current.hop_count + 1
      if new_gain > neighbor.gain or (new_gain == neighbor.gain and new_hop_count < neighbor.hop_count):
        neighbor.gain = new_gain
        neighbor.hop_count = new_hop_count
        neighbor.previous = current

    # Rebuild max heap
    unvisited = [(-n.gain, n) for n in network.nodes.itervalues() if not n.visited]
    heapq.heapify(unvisited)

  # Build route backwards from destination
  route = [end]
  current = end
  while current != start:
    route = [current.previous] + route
    current = current.previous
  return route, end.gain

''' Given network and list of flows, assign routes to flows while balancing network utilization '''
def balance_network(network):
  #  1. Get list of flows = {(source, destination)}
  #  2. Group flows by destination
  #  3. For each group in random order, for each flow in random order, find best route r = {(source, ..., destination)}
  #  4. Apply route changes to nodes

  logging.debug("--- Begin flow routing ---")

  #TODO: Parse flow table to obtain flows
  class Flow:
    def __init__(self, start, end):
      self.start = start
      self.end = end
  flows = []

#  flows = {} # Destination -> List of Flows
#
#  ip_addrs = {} # IP address -> alias 
#  for node in network.nodes.itervalues():
#    for name, iface in node.interfaces.iteritems():
#      ip_addrs[iface['addr']] = node
#
#  for node in network.nodes.itervalues():
#    for connection in node.flows['connections']:
#      for field in ['src2dst', 'dst2src']:
#        m = re.match(r'(.*):\d+ -> (.*):\d+', connection[field]['flow'])
#        if m:
#           src = m.group(1)
#           dst = m.group(2)
#           if dst not in flows:
#             flows[dst] = [Flow(src, dst)]
#           else:
#             found = False
#             for flow in flows[dst]:
#               if flow.start == src:
#                 found = True
#
#             if not found:
#               flows[dst] += [Flow(src, dst)]

  c_max = network.c_max

  flow_routes = {}
  flow_route_Q = {}      # Flow instance -> Q(r)
  edge_flows = {}        # Edge -> List of flows
  edge_capacities = {}   # Edge -> Capacity
  Q = {}                 # Edge -> Q(e)

  # Initialize Q
  for node in network.nodes.itervalues():
    for neighbor, link in node.neighbors.iteritems():
      Q[edge_key(node, neighbor)] = link.capacity / c_max

  #TODO: Group flows by destination and choose randomly
  for i in range(len(flows)):
    cur_flow = flows[i]

    # Update Q for next flow
    if i > 0:
      Q = {}
      for node in network.nodes.itervalues():
        for neighbor, link in node.neighbors.iteritems():
          key = edge_key(node, neighbor)
          if key in Q:
            continue  # Q(e) already set

          edge_capacities[key] = capacity

          # Compute remaining capacity from other flows using this edge
          utilization = 0
          flow_count = 1  # If new flow uses this edge
          if key in edge_flows:
            for flow in edge_flows[key]:
              utilization += flow_route_Q[key]
              flow_count += 1
          q_remaining = capacity - utilization

          if flow_count > 1:
            fair_share = capacity / (c_max * flow_count)
          else:
            fair_share = capacity / c_max

          Q[key] = max(q_remaining, fair_share)

    # Find best path using Q
    route, route_Q = find_best_path(network, Q, cur_flow.start, cur_flow.end)
    flow_routes[cur_flow] = route

    # Update flow utilization and edge assignments
    flow_route_Q[cur_flow] = route_Q
    for i in range(len(route) - 1):
      key = edge_key(route[i], route[i+1])

      # Update Q(r) of other flows if we introduce a new bottleneck by adding this flow to the edge
      if key in edge_flows:
        num_flows = len(edge_flows[key]) + 1
        fair_share = edge_capacities[key] / (c_max * num_flows)
        for flow in edge_flows[key]:
          if fair_share < flow_route_Q[flow]:
            flow_route_Q[flow] = fair_share

      # Add own flow to route
      if key in edge_flows:
        edge_flows[key] += [cur_flow]
      else:
        edge_flows[key] = [cur_flow]

  #TODO: Process flow_routes for each flow and update routing tables

  logging.debug("--- Flow routing complete ---")


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
  # Handle a single client.
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

    except socket.error, exc:
      logging.debug('Lost connection from {0}'.format(addr))
      return


def server_worker(done_event, network, args):
  # Initialize server for receiving updates.
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.bind((args.controller_ip, args.controller_port))
  s.settimeout(5)
  s.listen(5)
  logging.debug('Listening on port {0} for incoming connections'.format(args.controller_port))

  # Create one receive worker thread for each client.
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
      clients.append((t, conn))

    # Refresh client list for alive threads.
    done_threads = []
    for t, conn in clients:
      if not t.isAlive():
        conn.close()
        done_threads.append(t)
    clients = [c for c in clients if c[0] not in done_threads]
    # Yield thread.
    time.sleep(0)

  # Refresh network.
  for t, conn in clients:
    conn.close()
    t.join()


def network_refresh_worker(done_event, network):
  while not done_event.is_set():
    time.sleep(NETWORK_REFRESH_ITVL)
    network.refresh_node_list()


def input_worker(done_event, network, cmdargs):
  parser = argparse.ArgumentParser('')
  subparsers = parser.add_subparsers(dest='subparser')
  exit_parser = subparsers.add_parser('exit')
  getstate_parser = subparsers.add_parser('state')
  balance_parser = subparsers.add_parser('run')
  route_parser = subparsers.add_parser('route')
  route_parser.add_argument('command', choices=['add', 'delete'])
  route_parser.add_argument('node')
  route_parser.add_argument('destination')
  route_parser.add_argument('-i', '--interface')
  route_parser.add_argument('-g', '--gateway')

  while not done_event.is_set():
    cmd = shlex.split(raw_input('> ').strip())
    if len(cmd) <= 0:
      continue

    try:
      args = parser.parse_args(cmd)
      if args.subparser == 'exit':
        done_event.set()
      elif args.subparser == 'state':
        logging.debug(network)
      elif args.subparser == 'route':
        route_change = update_pb2.RouteChange()

        if args.command == 'add':
          route_change.command = update_pb2.RouteChange.ADD;
        elif args.command == 'delete':
          route_change.command = update_pb2.RouteChange.DELETE;

        route_change.destination = args.destination

        if args.interface:
          route_change.interface = args.interface
        if args.gateway:
          route_change.gateway

        route_change_str = route_change.SerializeToString()
        route_change_len = struct.pack('>L', len(route_change_str))
        message = route_change_len + route_change_str
        res = network.send_from_node(args.node, message)
        logging.debug(res)
      elif args.subparser == 'run':
        balance_network(network)
    except:
      pass


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
  t2 = threading.Thread(target=network_refresh_worker, args=[done_event, network])
  t3 = threading.Thread(target=input_worker, args=[done_event, network, args])
  threads.extend([t1, t2, t3])

  for t in threads:
    t.start()

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
