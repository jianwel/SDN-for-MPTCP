import fcntl
import select
import socket
import struct
import threading
import time
import traceback

BROADCAST_PORT = 36500
HELLO_INTERVAL = 2.0
HELLO_TIMEOUT = 10.0

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
def handle_recv_msg(msg, addr):
  msg = msg.strip()
  if msg == "HELLO":
    if addr not in neighbors_interface:
      print "Got new!", addr
    else:
      print "Refresh!", addr
    neighbors_interface[addr] = 'eth0'
    neighbors_last_refresh[addr] = time.time()
  else:
    print "Unhandled message from", addr, "-", msg


''' Listen for HELLO messages to either discover or refresh neighbors.
    If a neighbor isn't heard from in HELLO_TIMEOUT seconds, remove
    that address from the known list of neighbors.
'''
def listen_worker(done_event):
  # create broadcast socket
  # (taken from http://www.java2s.com/Code/Python/Network/UDPBroadcastServer.htm)
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  s.setblocking(0)
  #s.bind((get_ip_address("eth0"), BROADCAST_PORT))
  s.bind(('', BROADCAST_PORT))

  print "Listening on port", BROADCAST_PORT

  own_addr = str(get_ip_address("eth0"))

  while not done_event.is_set():
    try:
      # Read messages
      read_ready, _, _ = select.select([s], [], [], 0)
      if read_ready:
        msg, addr = s.recvfrom(1024)
        if addr[0] == own_addr:
          continue
        handle_recv_msg(msg, addr)

      # Timeout unheard neighbors
      cur_time = time.time()
      neighbors_expired = []
      for addr, last_refresh in neighbors_last_refresh.iteritems():
        if cur_time - last_refresh >= HELLO_TIMEOUT:
          neighbors_expired.append(addr)
      for addr in neighbors_expired:
        print "Lost neighbor:", addr
        del neighbors_interface[addr]
        del neighbors_last_refresh[addr]
      
    except (KeyboardInterrupt, SystemExit):  # needed?
      raise
    except:
      traceback.print_exc()


''' Periodically send HELLO to all neighbors.
'''
def hello_worker(done_event):
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  s.setblocking(0)
  addr = ('<broadcast>', BROADCAST_PORT)

  while not done_event.is_set():
    try:
      s.sendto("HELLO", addr)
      time.sleep(HELLO_INTERVAL)
    except (KeyboardInterrupt, SystemExit):  # needed?
      raise
    except:
      traceback.print_exc()


def main():
  done_event = threading.Event()
  t1 = threading.Thread(target=listen_worker, args=[done_event])
  t1.start()
  t2 = threading.Thread(target=hello_worker, args=[done_event])
  t2.start()

  try:
    while True:
      time.sleep(0.1)
  except KeyboardInterrupt:
    print "  Closing..."
    done_event.set()
    t1.join()

if __name__ == '__main__':
  main()

