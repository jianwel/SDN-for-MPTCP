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

file_path = os.path.dirname(sys.argv[0])
protobuf_path = os.path.abspath(os.path.join(file_path, '../protobuf'))
sys.path.append(protobuf_path)
import update_pb2

CONTROLLER_IP = ''
CONTROLLER_PORT = 36502

NODE_EXPIRE_TIME = 5.0

DEBUG_LOG = True
DEBUG_STDOUT = True
DEBUG_FILEPATH = "/tmp/sdncontroller.txt"

debug_file = None


''' Network class defining a graph of nodes '''
class Network:
  def __init__(self):
    self.nodes = []
    self.nodes_dict = {}  # IP -> Node instance
    self.nodes_lock = threading.Lock()
    
  def __str__(self):
    return self.debug_print()
    
  def update_node(self, ip, report):
    self.nodes_lock.acquire(True)
    
    if ip in self.nodes_dict:
      node = self.nodes_dict[ip]
      node.last_update = time.time()
    else:
      node = Node(ip, self)
      self.nodes.append(node)
      self.nodes_dict[ip] = node
      
    node_neighbors = []
    if len(report.neighbors) > 0:
      for neighbor in report.neighbors:
        if neighbor.ip in self.nodes_dict:
          node_neighbors.append(self.nodes_dict[neighbor.ip])
    node.neighbors = node_neighbors
    
    self.refresh_node_list()  #TODO: More efficient way of removing lost clients?
    
    self.nodes_lock.release()
  
  def refresh_node_list(self):
    if len(self.nodes) <= 1:
      return
      
    nodes_expired = []
    
    # Expire nodes that do not appear in any neighbor's list
    for node in self.nodes:
      if time.time() - node.last_update >= NODE_EXPIRE_TIME:
        found = False
        for neighbor in node.neighbors:
          if node in neighbor.neighbors:
            found = True
            break
        if not found:
          nodes_expired.append(node)
        
    for node in nodes_expired:
      self.nodes.remove(node)
      del self.nodes_dict[node.ip]
  
  def debug_print(self):
    self.nodes_lock.acquire(True)
    graph = 'Network (Size ' + str(len(self.nodes)) + ')\n'
    for node in self.nodes:
      graph += str(node) + ' -> ' + ', '.join([str(n) for n in node.neighbors]) + '\n'
    self.nodes_lock.release()
    return graph
  

''' Node class representing a client in the network '''
class Node:
  def __init__(self, ip, network):
    self.ip = ip
    self.neighbors = []
    self.network = network
    self.last_update = time.time()
    
  def __str__(self):
    return self.ip


def log(*args):
  if DEBUG_LOG:
    msg = ' '.join([str(a) for a in args])

    if DEBUG_STDOUT:
      print msg
    else:
      global debug_file
      if not debug_file:
        debug_file = open(DEBUG_FILEPATH, 'w+')
      debug_file.write(msg + '\n')

def receive_worker(conn, addr, network, done_event):
  # Handle a single client
  while not done_event.is_set():
    try:
      msg = conn.recv(1024)
      if len(msg) == 0:
        log("Lost connection from", addr)
        return

      report = update_pb2.Report()
      report.ParseFromString(msg)

      log("Report from", addr, ": time =", report.timestamp)
      
      network.update_node(addr[0], report)

      '''
      if len(report.neighbors) > 0:
        for neighbor in report.neighbors:
          log('  ', neighbor.ip, 'RTT:', neighbor.rtt)
      else:
        log("(blank)")
      '''

    except socket.error, exc:
      log("Lost connection from", addr)
      return

def server_worker(controller_ip, controller_port, network, done_event):
  # Initialize server for receiving updates
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.bind((controller_ip, controller_port))
  s.settimeout(5)
  s.listen(5)
  log("Listening on port", CONTROLLER_PORT, "for incoming connections")

  # Create one receive worker thread for each client
  clients = []
  while not done_event.is_set():
    if len(clients) < 5:
      try:
        conn, addr = s.accept()
      except socket.timeout:
        continue
      except socket.error, exc:
        log("Accept error:", exc)
        s.close()
        return

      log("Got connection from", addr)
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
    log(network)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('-c', '--controller-ip', default=CONTROLLER_IP, help='controller IP address')
  parser.add_argument('-p', '--controller-port', default=CONTROLLER_PORT, help='controller port')
  args = parser.parse_args()

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
    print "Closing..."
    done_event.set()
    for t in threads:
      t.join()

if __name__ == '__main__':
  main()
