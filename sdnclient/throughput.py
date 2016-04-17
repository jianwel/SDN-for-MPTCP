#!/usr/bin/python

import argparse
import signal
import sys
import subprocess
import os
import shlex
import re
import json

def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

def run_tcpdump(args):
    command = ('sudo tcpdump -i {interface} -s 65535 -n -B 4096 '
               '-w {pcap_file} -c {packet_count}').format(
        interface=args.interface,
        pcap_file=args.pcap_file,
        packet_count=args.packet_count)
    subprocess.check_call(shlex.split(command))

def run_captcp(args):
    if not os.path.isdir(args.output_dir):
        os.mkdir(args.output_dir)

    with open(os.path.join(args.output_dir, 'statistic.data'), 'w') as stat:
        command = ('captcp statistic {pcap_file}').format(
            pcap_file=args.pcap_file)
        ps = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE)

        command = ('sed -r -e "s/\x1B\[([0-9]{1,2}(;[0-9]{1,2})*)?[m|K]//g" '
                   '-e "s/^\s*//" -e "s/\s*$//" '
                   '-e "s/\( */(/" -e "s/\s{2}\s*/  /g"')
        subprocess.check_call(shlex.split(command), stdin=ps.stdout, stdout=stat)

def generate_json(args):
    json_obj = {}
    with open(os.path.join(args.output_dir, 'statistic.data'), 'r') as stat:
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

def signal_handler(signal, frame):
    sys.exit(0)

def main():
    if os.getuid() != 0:
        sys.exit('must be run as root')

    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser()
    parser.add_argument('interface', help='interface to capture')
    parser.add_argument('-p', '--pcap_file', default='tmp.pcap',
                        help='pcap file to create and analyze')
    parser.add_argument('-c', '--packet-count', type=int, default=10000,
                        help='tcpdump packet count')
    parser.add_argument('-o', '--output-dir', default='output',
                        help='captcp output directory')
    parser.add_argument('-j', '--json-file', default='captcp.json',
                        help='captcp json file')
    parser.add_argument('-i', '--infinite', action='store_true',
                        help='run infinite loop')
    args = parser.parse_args()

    if args.infinite:
        while True:
            run_tcpdump(args)
            run_captcp(args)
            generate_json(args)
    else:
        run_tcpdump(args)
        run_captcp(args)
        generate_json(args)

if __name__ == '__main__':
    main()
