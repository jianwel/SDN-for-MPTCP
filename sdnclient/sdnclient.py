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
import subprocess
import shlex
import re

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

neighbors = {} # IP -> {alias, interface, capacity, last_refresh, rtt, response_count}

''' Return True if string is a number.
'''
def is_number(s):
  try:
    float(s)
    return True
  except ValueError:
    return False


''' Return the capacity of the given interface in kbps
'''
def get_link_capacity(ifname):
  if ifname[:3] == "eth":
    # Get interface speed using ethtool
    ps = subprocess.Popen(('ethtool', ifname), stdout=subprocess.PIPE)
    output = subprocess.check_output(('grep', 'Speed'), stdin=ps.stdout)
    ps.wait()

    speed_str = output.split()[1]
    value_str = speed_str[:speed_str.find('b/s') - 1]
    units = speed_str[len(value_str):]

    if units == "Mb/s":
      return float(value_str) * 1000
    else:
      return float(value_str)

  elif ifname[:4] == "wlan":
    return 56000  # Assume 56Mb/s for IEEE802.11b

  return 0


''' Construct and return routing table object.
'''
def get_routes():
  routes = []
  command = 'route -n'
  ip_regex = '\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'
  ps = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE)
  for line in ps.stdout:
    m = re.match('(?P<destination>{ip})\s+(?P<gateway>{ip})\s+(?P<genmask>{ip})\s+(?P<flags>\S+)\s+(?P<metric>\S+)\s+(?P<ref>\S+)\s+(?P<use>\S+)\s+(?P<iface>\S+)'.format(ip=ip_regex), line)
    if m:
      routes.append(m.groupdict())

  return routes


''' Construct and return interfaces object.
'''
def get_interfaces():
  interfaces = {}
  for iface in netifaces.interfaces():
    ifaddr = netifaces.ifaddresses(iface)
    if netifaces.AF_INET in ifaddr:
      interfaces[iface] = ifaddr[netifaces.AF_INET][0]

  return interfaces


''' Handle received messages from neighbors.
'''
def handle_recv_msg(msg, host_addr, own_addrs, bcast_iface):
  if host_addr not in neighbors:
    neighbors[host_addr] = {'alias': '', 'interface': bcast_iface['name'], 'capacity': get_link_capacity(bcast_iface['name']), 'last_refresh': time.time(), 'rtt': 0.0, 'response_count': 0}
  else:
    neighbors[host_addr]['last_refresh'] = time.time()

  update = update_pb2.Update()
  update.ParseFromString(msg)
  if update.utype == update_pb2.Update.QUERY:
    logging.debug('Got query! {0}'.format(host_addr))

    response = update_pb2.Update()
    response.timestamp = update.timestamp
    response.utype = update_pb2.Update.RESPONSE
    response.ip = update.ip
    response_addr = (bcast_iface['ipv4']['broadcast'], BROADCAST_PORT)
    bcast_iface['socket'].sendto(response.SerializeToString(), response_addr)

    if update.HasField('alias'):
      neighbors[host_addr]['alias'] = update.alias
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
def listen_worker(done_event, bcast_ifaces):
  # Create broadcast sockets for each interface.
  own_addrs = []
  for bcast_iface in bcast_ifaces:
    # http://www.java2s.com/Code/Python/Network/UDPBroadcastServer.htm
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setblocking(0)
    s.bind((bcast_iface['ipv4']['broadcast'], BROADCAST_PORT))
    bcast_iface['socket'] = s
    own_addrs.append(bcast_iface['ipv4']['addr'])

  logging.debug('Listening on port {0}'.format(BROADCAST_PORT))

  while not done_event.is_set():
    try:
      # Read messages on each interface.
      for bcast_iface in bcast_ifaces:
        s = bcast_iface['socket']
        read_ready, _, _ = select.select([s], [], [], 0)
        if read_ready:
          msg, (host_addr, port) = s.recvfrom(1024)
          if host_addr in own_addrs:
            continue
          handle_recv_msg(msg, host_addr, own_addrs, bcast_iface)

      # Timeout unheard neighbors.
      cur_time = time.time()
      addrs_expired = []
      for addr, neighbor in neighbors.iteritems():
        if cur_time - neighbor['last_refresh'] >= QUERY_TIMEOUT:
          addrs_expired.append(addr)

      for addr in addrs_expired:
          logging.debug('Lost neighbor: {0}'.format(addr))
          del neighbors[addr]
    except:
      traceback.print_exc()
      done_event.set()


''' Periodically send QUERY to all neighbors.
'''
def query_worker(done_event, args, bcast_ifaces):
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  s.setblocking(0)

  while not done_event.is_set():
    try:
      query = update_pb2.Update()
      query.timestamp = '{:.6f}'.format(time.time())
      query.utype = update_pb2.Update.QUERY
      if len(args.alias) > 0:
        query.alias = args.alias

      for bcast_iface in bcast_ifaces:
        query.ip = bcast_iface['ipv4']['addr']
        bcast_addr = (bcast_iface['ipv4']['broadcast'], BROADCAST_PORT)
        s.sendto(query.SerializeToString(), bcast_addr)
      time.sleep(QUERY_INTERVAL)
    except:
      traceback.print_exc()
      done_event.set()


# http://eli.thegreenplace.net/2011/08/02/length-prefix-framing-for-protocol-buffers
def socket_read_n_time(sock, n, buf, timeout):
    ''' Read exactly n bytes from the socket.
        Raise RuntimeError if the connection closed before
        n bytes were read.
    '''
    start_time = time.time()
    time_diff = 0
    while n > 0:
      if time_diff > timeout:
        raise socket.timeout
      sock.settimeout(timeout - time_diff)
      data = sock.recv(n)
      if data == '':
        buf = ''
        break
      if not buf:
        buf = ''
      buf += data
      n -= len(data)
      if n > 0:
        time_diff = time.time() - start_time

    return buf, n


''' Connect to SDN controller and periodically send updates.
'''
def update_worker(done_event, args):
  def connect():
    try:
      logging.debug('Attempting to connect to SDN controller...')
      s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      s.settimeout(CONTROLLER_CONNECT_TIMEOUT)
      s.connect((args.controller_ip, args.controller_port))
      logging.debug('Connected to SDN controller.')
      return s
    except socket.error, exc:
      return None

  connected = False
  len_buf = None
  msg_len = None
  msg = None
  while not done_event.is_set():
    if not connected:
      len_buf = None
      msg_len = None
      msg = None
      s = connect()
      if not s:
        time.sleep(CONTROLLER_WAIT_CONNECT)
        continue
      connected = True

    # Construct neighbor report.
    report = update_pb2.Report()
    report.timestamp = '{:.6f}'.format(time.time())
    if len(args.alias) > 0:
      report.alias = args.alias
    # IP -> {alias, last_refresh, rtt, response_count}
    for ip in neighbors:
      neighbor = report.neighbors.add()
      neighbor.ip = ip
      neighbor.rtt = '{:6f}'.format(neighbors[ip]['rtt'])
      if len(neighbors[ip]['alias']) > 0:
        neighbor.alias = neighbors[ip]['alias']
      if len(neighbors[ip]['interface']) > 0:
        neighbor.interface = neighbors[ip]['interface']
      if neighbors[ip]['capacity'] != '0':
        neighbor.capacity = '{:6f}'.format(neighbors[ip]['capacity'])

    # Add flow info.
    json_file_path = os.path.join(args.output_dir, args.json_file)
    if os.path.exists(json_file_path):
      with open(json_file_path, 'r') as json_file:
        json_obj = json.load(json_file)
        json_str = json.dumps(json_obj)
        report.flows = json_str

    # Add routes info.
    routes = get_routes()
    json_str = json.dumps(routes)
    report.routes = json_str

    # Add interfaces info
    ifaces = get_interfaces()
    json_str = json.dumps(ifaces)
    report.interfaces = json_str

    try:
      report_str = report.SerializeToString()
      report_len = struct.pack('>L', len(report_str))
      s.send(report_len + report_str)
      logging.debug('Sent report to controller.')
    except socket.error, exc:
      logging.debug('Failed to send update message. Reconnecting...')
      s.close()
      connected = False
      continue

    last_send_time = time.time()
    timeout = CONTROLLER_UPDATE_INTERVAL
    try:
      while timeout > 0:
        if not len_buf:
          # Assume short length message will not be truncated.
          time_elapsed = time.time() - last_send_time
          timeout = CONTROLLER_UPDATE_INTERVAL - time_elapsed
          len_buf, ignored = socket_read_n_time(s, 4, None, timeout)
          if len(len_buf) == 0:
            logging.debug('Lost connection to controller. Reconnecting...')
            s.close()
            connected = False
            break
          msg_len = struct.unpack('>L', len_buf)[0]

        time_elapsed = time.time() - last_send_time
        timeout = CONTROLLER_UPDATE_INTERVAL - time_elapsed
        msg, msg_len = socket_read_n_time(s, msg_len, msg, timeout)
        if msg_len == 0:
          # Handle complete message, then reset variables.
          route_change = update_pb2.RouteChange()
          route_change.ParseFromString(msg)

          route_cmd = ''
          if route_change.command == update_pb2.RouteChange.ADD:
            route_cmd = 'add'
          elif route_change.command == update_pb2.RouteChange.DELETE:
            route_cmd = 'delete'

          next_hop_opts = ''
          if route_change.HasField('interface'):
            next_hop_opts += 'dev ' + route_change.interface + ' '
          if route_change.HasField('gateway'):
            next_hop_opts += 'via ' + route_change.gateway + ' '

          if route_cmd == '' or next_hop_opts == '':
            logging.debug('Ignoring invalid or unimplemented route change message...')
          else:
            command = ('sudo ip route {route_cmd} {dst} {next_hop_opts}').format(
              route_cmd=route_cmd,
              dst=route_change.destination,
              next_hop_opts=next_hop_opts).strip()
            with open(os.devnull, 'w') as devnull:
              rc = subprocess.call(shlex.split(command), stderr=devnull)
              logging.debug(('Executed command "{command}" with return code {rc}.').format(command=command, rc=rc))

          len_buf = None
          msg_len = None
          msg = None
    except socket.timeout, exc:
      pass
    except socket.error, exc:
      logging.debug('Socket error during receive from controller. Reconnecting...')
      s.close()
      connected = False


''' Run tcpdump and captcp continuously.
'''
def flow_worker(done_event, args):
  pcap_file = 'tcpdump.pcap'
  stats_file = 'stats.dat'

  if not os.path.isdir(args.output_dir):
    os.mkdir(args.output_dir)

  while not done_event.is_set():
    try:
      # Run tcpdump.
      command = ('sudo tcpdump -i any -s 96 -n -B 4096 '
             '-w {pcap_file} -c {packet_count} tcp').format(
        pcap_file=os.path.join(args.output_dir, pcap_file),
        packet_count=args.packet_count)
      with open(os.devnull, 'w') as devnull:
        ps = subprocess.Popen(shlex.split(command), stderr=subprocess.PIPE)
        time.sleep(args.run_time)
        ps.terminate()
        for line in ps.stderr:
          m = re.match('(\d+) packets captured', line)
          if m:
            packets_captured = int(m.group(1))

      # Run captcp.
      json_obj = {}
      stats_path = os.path.join(args.output_dir, stats_file)
      if packets_captured > 0:
        with open(stats_path, 'w') as stat, open(os.devnull, 'w') as devnull:
          command = ('sudo captcp statistic {pcap_file}').format(
            pcap_file=os.path.join(args.output_dir, pcap_file))
          ps = subprocess.Popen(shlex.split(command), stderr=devnull,
            stdout=subprocess.PIPE)

          command = ('sed -r -e "s/\x1B\[([0-9]{1,2}(;[0-9]{1,2})*)?[m|K]//g" '
                 '-e "s/^\s*//" -e "s/\s*$//" '
                 '-e "s/\( */(/" -e "s/\s{2}\s*/  /g"')
          subprocess.check_call(shlex.split(command), stdin=ps.stdout,
            stdout=stat)

        with open(stats_path, 'r') as stat:
          lines = (line.strip() for line in stat)
          lines = (line for line in lines if line)
          current = json_obj
          for line in lines:
            fields = re.split('  |: ', line.strip())
            if fields[0] == 'General:':
              current = {}
              json_obj['general'] = current
            elif fields[0] == 'Network Layer' or \
                fields[0] == 'Transport Layer':
              pascalcased = ''.join(
                x for x in fields[0].title() if not x.isspace())
              camelcased = pascalcased[0].lower() + pascalcased[1:]
              tmp = {}
              json_obj['general'][camelcased] = tmp
              current = tmp
            elif fields[0] == 'Connections:':
              json_obj['connections'] = []
            elif is_number(fields[0]) and 'connections' in json_obj:
              current = {'src2dst': {}, 'dst2src': {}}
              json_obj['connections'].append(current)
            elif len(fields) == 2:
              m = re.match(r'^Flow \d+.(\d+)', fields[0])
              if m:
                if m.group(1) == '1':
                  current['src2dst']['flow'] = fields[1]
                elif m.group(1) == '2':
                  current['dst2src']['flow'] = fields[1]
              else:
                pascalcased = ''.join(
                  x for x in fields[0].title() if not x.isspace())
                camelcased = pascalcased[0].lower() + pascalcased[1:]
                current[camelcased] = fields[1].strip()
            elif len(fields) == 4:
              pascalcased = ''.join(
                x for x in fields[0].title() if not x.isspace())
              camelcased = pascalcased[0].lower() + pascalcased[1:]
              current['src2dst'][camelcased] = fields[1].strip()

              pascalcased = ''.join(
                x for x in fields[2].title() if not x.isspace())
              camelcased = pascalcased[0].lower() + pascalcased[1:]
              current['dst2src'][camelcased] = fields[3].strip()

      with open(os.path.join(args.output_dir, args.json_file), 'w') as json_file:
        json.dump(json_obj, json_file, sort_keys = True, indent = 4, ensure_ascii = False)
        if os.path.exists(stats_path):
          os.remove(stats_path)
    except:
      traceback.print_exc()
      done_event.set()


def main():
  if os.getuid() != 0:
    sys.exit('must be run as root')

  parser = argparse.ArgumentParser()
  parser.add_argument('-c', '--controller-ip', default=CONTROLLER_IP, help='controller IP address')
  parser.add_argument('-p', '--controller-port', default=CONTROLLER_PORT, help='controller port')
  parser.add_argument('-d', '--debug-file', default=DEBUG_FILEPATH, help='debug file')
  parser.add_argument('-D', '--disable-debug', action='store_true', help='disable debug logging')
  parser.add_argument('-P', '--print-stdout', action='store_true', help='print debug info to stdout')
  parser.add_argument('-o', '--output-dir', default='output', help='captcp output directory')
  parser.add_argument('-j', '--json-file', default='captcp.json', help='captcp json file')
  parser.add_argument('-a', '--alias', default='', help='unique alias in network')
  parser.add_argument('-t', '--run-time', type=int, default=2,
                      help='tcpdump run time')
  parser.add_argument('-k', '--packet-count', type=int, default=1000,
                      help='tcpdump packet count')
  args = parser.parse_args()

  if not args.disable_debug:
    logging.basicConfig(level=logging.DEBUG, filename=args.debug_file)

  if args.print_stdout:
    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.DEBUG)
    streamHandler = logging.StreamHandler(sys.stdout)
    streamHandler.setLevel(logging.DEBUG)
    rootLogger.addHandler(streamHandler)

  # http://stackoverflow.com/questions/819355/how-can-i-check-if-an-ip-is-in-a-network-in-python
  bcast_ifaces = []
  for iface in netifaces.interfaces():
    ifaddr = netifaces.ifaddresses(iface)
    if netifaces.AF_INET in ifaddr:
      ipv4 = ifaddr[netifaces.AF_INET][0]
      addr_s = ipv4['addr']
      addr = struct.unpack('>L', socket.inet_aton(addr_s))[0]
      net = struct.unpack('>L', socket.inet_aton(BROADCAST_NET_S))[0]
      mask = (0xffffffff << (32 - BROADCAST_MASK_BITS)) & 0xffffffff
      if (addr & mask) == (net & mask):
        bcast_ifaces.append({'name': iface, 'ipv4': ipv4})

  done_event = threading.Event()
  threads = []
  t1 = threading.Thread(target=listen_worker, args=[done_event, bcast_ifaces])
  t2 = threading.Thread(target=query_worker, args=[done_event, args, bcast_ifaces])
  t3 = threading.Thread(target=update_worker, args=[done_event, args])
  t4 = threading.Thread(target=flow_worker, args=[done_event, args])
  threads.extend([t1, t2, t3, t4])

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
