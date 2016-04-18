#!/usr/bin/python

import os
import sys
import fcntl
import select
import socket
import struct
import threading
import time
import traceback
import argparse

file_path = os.path.dirname(sys.argv[0])
protobuf_path = os.path.abspath(os.path.join(file_path, '../protobuf'))
sys.path.append(protobuf_path)
import update_pb2

BROADCAST_PORT = 36500
QUERY_INTERVAL = 2.0
QUERY_TIMEOUT = 10.0

CONTROLLER_IP = '169.232.191.223'
CONTROLLER_PORT = 36502
CONTROLLER_WAIT_CONNECT = 3.0
CONTROLLER_UPDATE_INTERVAL = 3.0

neighbors_interface = dict()     # IP -> interface name
neighbors_last_refresh = dict()  # IP -> last refresh time


''' Returns the associated IP of an interface name
'''
def get_ip_address(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(fcntl.ioctl(
        s.fileno(),
        0x8915,  # SIOCGIFADDR
        struct.pack('256s', ifname[:15])
    )[20:24])


''' Handle received messages from neighbors
'''
def handle_recv_msg(msg, addr, s):
  msg = msg.strip()
  update = update_pb2.Update()
  update.ParseFromString(msg)
  if update.utype == update_pb2.Update.QUERY:
    if addr not in neighbors_interface:
      print "Got new!", addr
    else:
      print "Refresh!", addr
    #TODO: Store interface over which QUERY was received
    neighbors_interface[addr] = 'eth0'
    neighbors_last_refresh[addr] = time.time()

    bcast_addr = ('<broadcast>', BROADCAST_PORT)
    response = update_pb2.Update()
    response.utype = update_pb2.Update.RESPONSE
    response.data = ''
    s.sendto(response.SerializeToString(), bcast_addr)
  elif update.utype == update_pb2.Update.RESPONSE:
    print "Got response!", addr
  else:
    print "Unhandled message from", addr


''' Listen for QUERY messages to either discover or refresh neighbors.
    If a neighbor isn't heard from in QUERY_TIMEOUT seconds, remove
    that address from the known list of neighbors.
'''
def listen_worker(own_interface, done_event):
  # create broadcast socket
  # (taken from http://www.java2s.com/Code/Python/Network/UDPBroadcastServer.htm)
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  s.setblocking(0)
  #s.bind((get_ip_address("eth0"), BROADCAST_PORT))
  s.bind(('', BROADCAST_PORT))

  print "Listening on port", BROADCAST_PORT

  own_addr = str(get_ip_address(own_interface))

  while not done_event.is_set():
    try:
      # Read messages
      read_ready, _, _ = select.select([s], [], [], 0)
      if read_ready:
        msg, addr = s.recvfrom(1024)
        if addr[0] == own_addr:
          continue
        handle_recv_msg(msg, addr, s)

      # Timeout unheard neighbors
      cur_time = time.time()
      neighbors_expired = []
      for addr, last_refresh in neighbors_last_refresh.iteritems():
        if cur_time - last_refresh >= QUERY_TIMEOUT:
          neighbors_expired.append(addr)
      for addr in neighbors_expired:
        print "Lost neighbor:", addr
        del neighbors_interface[addr]
        del neighbors_last_refresh[addr]
      
    except (KeyboardInterrupt, SystemExit):  # needed?
      raise
    except:
      traceback.print_exc()


''' Periodically send QUERY to all neighbors.
'''
def hello_worker(done_event):
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  s.setblocking(0)
  addr = ('<broadcast>', BROADCAST_PORT)

  while not done_event.is_set():
    try:
      query = update_pb2.Update()
      query.utype = update_pb2.Update.QUERY
      query.data = str(time.time())
      s.sendto(query.SerializeToString(), addr)
      time.sleep(QUERY_INTERVAL)
    except (KeyboardInterrupt, SystemExit):  # needed?
      raise
    except:
      traceback.print_exc()


''' Connect to SDN controller and periodically send updates '''
def update_worker(controller_addr, controller_port, done_event):
  def connect():
    try:
      print "Attempting to connect to SDN controller..."
      s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      s.connect((controller_addr, controller_port))
      print "Connected to SDN controller."
    except socket.error, exc:
      return None
    return s

  connected = False
  while not done_event.is_set():
    if not connected:
      s = connect()
      if not s:
        time.sleep(CONTROLLER_WAIT_CONNECT)
        continue
      connected = True

    # Write a test message
    try:
      bytes_sent = s.send("What's up")
      if bytes_sent == 0:
        print "Failed to send update message. Reconnecting..."
        s.close()
        connected = False
    except socket.error, exc:
      print "Update send failed. Reconnecting..."
      s.close()
      connected = False

    if bytes_sent > 0:
      print "Sent update message."
      time.sleep(CONTROLLER_UPDATE_INTERVAL)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('-i', '--interface', default='eth0', help='broadcast interface')
  parser.add_argument('-c', '--controller-ip', default=CONTROLLER_IP, help='controller IP address')
  parser.add_argument('-p', '--controller-port', default=CONTROLLER_PORT, help='controller port')
  args = parser.parse_args()

  done_event = threading.Event()
  threads = []
  t1 = threading.Thread(target=listen_worker, args=[args.interface, done_event])
  t1.start()
  t2 = threading.Thread(target=hello_worker, args=[done_event])
  t2.start()
  #t3 = threading.Thread(target=update_worker, args=[args.controller_ip, args.controller_port, done_event])
  #t3.start()
  threads.extend([t1, t2])

  try:
    while True:
      time.sleep(0.1)
  except KeyboardInterrupt:
    print "  Closing..."
    done_event.set()
    for t in threads:
      t.join()

if __name__ == '__main__':
  main()
