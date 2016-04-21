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
import json

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

neighbors = {} # IP -> {interface, last_refresh, rtt, response_count}
own_addr = ''

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
def handle_recv_msg(msg, addr, s, own_addr):
  if addr not in neighbors:
    #TODO: Store interface over which QUERY was received
    neighbors[addr] = {'interface': 'eth0', 'last_refresh': time.time(), 'rtt': 0.0, 'response_count': 0}
  else:
    neighbors[addr]['last_refresh'] = time.time()

  update = update_pb2.Update()
  update.ParseFromString(msg)
  if update.utype == update_pb2.Update.QUERY:
    print "Got query!", addr

    bcast_addr = ('<broadcast>', BROADCAST_PORT)
    response = update_pb2.Update()
    response.timestamp = update.timestamp
    response.utype = update_pb2.Update.RESPONSE
    response.ip = addr
    s.sendto(response.SerializeToString(), bcast_addr)
  elif update.utype == update_pb2.Update.RESPONSE:
    print "Got response!", addr

    if update.ip == own_addr:
      new_rtt = time.time() - float(update.timestamp)
      prev_rtt = neighbors[addr]['rtt']
      prev_count = neighbors[addr]['response_count']
      neighbors[addr]['rtt'] = (new_rtt + prev_rtt * prev_count) / (prev_count + 1)
      neighbors[addr]['response_count'] += 1
      print addr, ' RTT =', neighbors[addr]['rtt']
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
  s.bind(('', BROADCAST_PORT))

  response_s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  response_s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  response_s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  response_s.setblocking(0)

  print "Listening on port", BROADCAST_PORT

  own_addr = str(get_ip_address(own_interface))

  while not done_event.is_set():
    try:
      # Read messages
      read_ready, _, _ = select.select([s], [], [], 0)
      if read_ready:
        msg, (host_addr, port) = s.recvfrom(1024)
        if host_addr == own_addr:
          continue
        handle_recv_msg(msg, host_addr, response_s, own_addr)

      # Timeout unheard neighbors
      cur_time = time.time()
      addrs_expired = []
      for addr, neighbor in neighbors.iteritems():
        if cur_time - neighbor['last_refresh'] >= QUERY_TIMEOUT:
          addrs_expired.append(addr)

      for addr in addrs_expired:
          print "Lost neighbor:", addr
          del neighbors[addr]

    except (KeyboardInterrupt, SystemExit):  # needed?
      raise
    except:
      traceback.print_exc()


''' Periodically send QUERY to all neighbors.
'''
def hello_worker(own_interface, done_event):
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  s.setblocking(0)
  bcast_addr = ('<broadcast>', BROADCAST_PORT)

  own_addr = str(get_ip_address(own_interface))

  while not done_event.is_set():
    try:
      query = update_pb2.Update()
      query.timestamp = '{:.6f}'.format(time.time())
      query.utype = update_pb2.Update.QUERY
      query.ip = own_addr
      s.sendto(query.SerializeToString(), bcast_addr)
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

    # Construct neighbor report
    report = update_pb2.Report()
    report.timestamp = time.time()
    # IP -> {interface, last_refresh, rtt, response_count}
    for ip in neighbors:
      neighbor = report.neighbors.add()
      neighbor.ip = ip
      neighbor.rtt = neighbors[ip]['rtt']

    try:
      bytes_sent = s.send(report.SerializeToString())
      if bytes_sent == 0:
        print "Failed to send update message. Reconnecting..."
        s.close()
        connected = False
    except socket.error, exc:
      print "Update send failed. Reconnecting..."
      s.close()
      connected = False

    if bytes_sent > 0:
      print "Sent report to controller."
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
  t2 = threading.Thread(target=hello_worker, args=[args.interface, done_event])
  t2.start()
  t3 = threading.Thread(target=update_worker, args=[args.controller_ip, args.controller_port, done_event])
  t3.start()
  threads.extend([t1, t2, t3])

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
