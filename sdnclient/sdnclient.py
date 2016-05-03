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
import logging
import netifaces

file_path = os.path.dirname(sys.argv[0])
protobuf_path = os.path.abspath(os.path.join(file_path, '../protobuf'))
sys.path.append(protobuf_path)
import update_pb2

BROADCAST_PORT = 36500
BROADCAST_NET_S = '10.0.0.0'
BROADCAST_MASK_BITS = 16
QUERY_INTERVAL = 2.0
QUERY_TIMEOUT = 10.0

CONTROLLER_IP = ''
CONTROLLER_PORT = 36502
CONTROLLER_CONNECT_TIMEOUT = 10.0
CONTROLLER_WAIT_CONNECT = 3.0
CONTROLLER_UPDATE_INTERVAL = 3.0

DEBUG_FILEPATH = '/tmp/sdnclient.log'

neighbors = {} # IP -> {last_refresh, rtt, response_count}


''' Handle received messages from neighbors
'''
def handle_recv_msg(msg, host_addr, own_addrs, res, bcast_ifaces):
  if host_addr not in neighbors:
    neighbors[host_addr] = {'last_refresh': time.time(), 'rtt': 0.0, 'response_count': 0}
  else:
    neighbors[host_addr]['last_refresh'] = time.time()

  update = update_pb2.Update()
  update.ParseFromString(msg)
  if update.utype == update_pb2.Update.QUERY:
    logging.debug('Got query! {0}'.format(host_addr))

    bcast_addr = ('<broadcast>', BROADCAST_PORT)
    response = update_pb2.Update()
    response.timestamp = update.timestamp
    response.utype = update_pb2.Update.RESPONSE
    for bcast_iface in bcast_ifaces:
      response.ip = bcast_iface['ipv4']['addr']
      bcast_addr = (bcast_iface['ipv4']['broadcast'], BROADCAST_PORT)
      res.sendto(response.SerializeToString(), bcast_addr)
  elif update.utype == update_pb2.Update.RESPONSE:
    logging.debug('Got response! {0}'.format(host_addr))

    if update.ip in own_addrs:
      new_rtt = time.time() - float(update.timestamp)
      prev_rtt = neighbors[host_addr]['rtt']
      prev_count = neighbors[host_addr]['response_count']
      neighbors[host_addr]['rtt'] = (new_rtt + prev_rtt * prev_count) / (prev_count + 1)
      neighbors[host_addr]['response_count'] += 1
      logging.debug('{0} RTT = {1}'.format(host_addr, neighbors[host_addr]['rtt']))
  else:
    logging.debug('Unhandled message from {0}'.format(host_addr))


''' Listen for QUERY messages to either discover or refresh neighbors.
    If a neighbor isn't heard from in QUERY_TIMEOUT seconds, remove
    that address from the known list of neighbors.
'''
def listen_worker(done_event):
  # http://www.java2s.com/Code/Python/Network/UDPBroadcastServer.htm
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  s.setblocking(0)
  s.bind(('', BROADCAST_PORT))

  res = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  res.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  res.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  res.setblocking(0)

  # http://stackoverflow.com/questions/819355/how-can-i-check-if-an-ip-is-in-a-network-in-python
  own_addrs = []
  bcast_ifaces = []
  for iface in netifaces.interfaces():
    ipv4 = netifaces.ifaddresses(iface)[netifaces.AF_INET][0]
    addr_s = ipv4['addr']
    own_addrs.append(addr_s)
    addr = struct.unpack('>L', socket.inet_aton(addr_s))[0]
    net = struct.unpack('>L', socket.inet_aton(BROADCAST_NET_S))[0]
    mask = (0xffffffff << (32 - BROADCAST_MASK_BITS)) & 0xffffffff
    if (addr & mask) == (net & mask):
      bcast_ifaces.append({'name': iface, 'ipv4': ipv4})

  logging.debug('Listening on port {0}'.format(BROADCAST_PORT))

  while not done_event.is_set():
    try:
      # Read messages
      read_ready, _, _ = select.select([s], [], [], 0)
      if read_ready:
        msg, (host_addr, port) = s.recvfrom(1024)
        if host_addr in own_addrs:
          continue
        handle_recv_msg(msg, host_addr, own_addrs, res, bcast_ifaces)

      # Timeout unheard neighbors
      cur_time = time.time()
      addrs_expired = []
      for addr, neighbor in neighbors.iteritems():
        if cur_time - neighbor['last_refresh'] >= QUERY_TIMEOUT:
          addrs_expired.append(addr)

      for addr in addrs_expired:
          logging.debug('Lost neighbor: {0}'.format(addr))
          del neighbors[addr]

    except (KeyboardInterrupt, SystemExit):  # needed?
      raise
    except:
      traceback.print_exc()


''' Periodically send QUERY to all neighbors.
'''
def query_worker(done_event):
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  s.setblocking(0)

  # http://stackoverflow.com/questions/819355/how-can-i-check-if-an-ip-is-in-a-network-in-python
  bcast_ifaces = []
  for iface in netifaces.interfaces():
    ipv4 = netifaces.ifaddresses(iface)[netifaces.AF_INET][0]
    addr_s = ipv4['addr']
    addr = struct.unpack('>L', socket.inet_aton(addr_s))[0]
    net = struct.unpack('>L', socket.inet_aton(BROADCAST_NET_S))[0]
    mask = (0xffffffff << (32 - BROADCAST_MASK_BITS)) & 0xffffffff
    if (addr & mask) == (net & mask):
      bcast_ifaces.append({'name': iface, 'ipv4': ipv4})

  while not done_event.is_set():
    try:
      query = update_pb2.Update()
      query.timestamp = '{:.6f}'.format(time.time())
      query.utype = update_pb2.Update.QUERY
      for bcast_iface in bcast_ifaces:
        query.ip = bcast_iface['ipv4']['addr']
        bcast_addr = (bcast_iface['ipv4']['broadcast'], BROADCAST_PORT)
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
      logging.debug('Attempting to connect to SDN controller...')
      s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      s.settimeout(CONTROLLER_CONNECT_TIMEOUT)
      s.connect((controller_addr, controller_port))
      logging.debug('Connected to SDN controller.')
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
    report.timestamp = '{:.6f}'.format(time.time())
    # IP -> {last_refresh, rtt, response_count}
    for ip in neighbors:
      neighbor = report.neighbors.add()
      neighbor.ip = ip
      neighbor.rtt = '{:6f}'.format(neighbors[ip]['rtt'])

    try:
      bytes_sent = s.send(report.SerializeToString())
      if bytes_sent == 0:
        logging.debug('Failed to send update message. Reconnecting...')
        s.close()
        connected = False
    except socket.error, exc:
      logging.debug('Update send failed. Reconnecting...')
      s.close()
      connected = False

    if bytes_sent > 0:
      logging.debug('Sent report to controller.')
      time.sleep(CONTROLLER_UPDATE_INTERVAL)


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

  done_event = threading.Event()
  threads = []
  t1 = threading.Thread(target=listen_worker, args=[done_event])
  t1.start()
  t2 = threading.Thread(target=query_worker, args=[done_event])
  t2.start()
  t3 = threading.Thread(target=update_worker, args=[args.controller_ip, args.controller_port, done_event])
  t3.start()
  threads.extend([t1, t2, t3])

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
