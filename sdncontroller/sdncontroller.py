#!/usr/bin/python

''' SDN Controller -- Receives router updates, constructs a stable view of the network, and makes routing changes to improve flow throughput '''

import json
import socket
import struct
import threading
import time
import argparse

CONTROLLER_IP = '169.232.191.223'
CONTROLLER_PORT = 36502


def receive_worker(conn, addr, done_event):
  # Handle a single client
  while not done_event.is_set():
    try:
      msg = conn.recv(64)
      if len(msg) == 0:
        print "Lost connection from", addr
        return
      print "Got message from", addr, "-", msg
    except socket.error, exc:
      print "Lost connection from", addr
      return

def server_worker(controller_ip, controller_port, done_event):
  # Initialize server for receiving updates
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.bind((controller_ip, controller_port))
  s.settimeout(5)
  s.listen(5)
  print "Listening on port", CONTROLLER_PORT, "for incoming connections"

  # Create one receive worker thread for each client
  clients = []
  while not done_event.is_set():
    if len(clients) < 5:
      try:
        conn, addr = s.accept()
      except socket.timeout:
        continue
      except socket.error, exc:
        print "Accept error:", exc
        s.close()
        return
      
      print "Got connection from", addr
      t = threading.Thread(target=receive_worker, args=[conn, addr, done_event])
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
  
  for t, conn, _ in clients:
    conn.close()
    t.join()


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('-c', '--controller-ip', default=CONTROLLER_IP, help='controller IP address')
  parser.add_argument('-p', '--controller-port', default=CONTROLLER_PORT, help='controller port')
  args = parser.parse_args()

  done_event = threading.Event()
  threads = []
  t1 = threading.Thread(target=server_worker, args=[args.controller_ip, args.controller_port, done_event])
  t1.start()
  threads.extend([t1])

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

