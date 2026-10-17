#!/usr/bin/env python3
"""A small client for the edit notification server, for trying it out:

	python3 notify/client.py localhost 7500
	python3 notify/client.py localhost 7500 --bbox -1.11,50.77,-1.03,50.86

It says hello, subscribes, and prints each notification as it comes. Needs
the msgpack package.
"""

import argparse
import itertools
import socket
import sys

import msgpack

class NotifyClient:
	"""A blocking client: requests wait for their answer, and notifications that
	arrive meanwhile are kept for next_notification."""

	def __init__(self, host, port, timeout=None):
		self.socket = socket.create_connection((host, port), timeout=timeout)
		self.unpacker = msgpack.Unpacker(raw=False)
		self.ids = itertools.count()
		self.waiting = []

	def _messages(self):
		while True:
			for message in self.unpacker:
				yield message
			data = self.socket.recv(65536)
			if not data:
				raise ConnectionError("The server closed the connection")
			self.unpacker.feed(data)

	def call(self, method, *params):
		"""Sends a request; returns its result, or raises RuntimeError with the error."""
		msgid = next(self.ids)
		self.socket.sendall(msgpack.packb([0, msgid, method, list(params)], use_bin_type=True))
		for message in self._messages():
			if message[0] == 1 and message[1] == msgid:
				if message[2] is not None:
					raise RuntimeError("{} ({})".format(message[2].get("message"), message[2].get("code")))
				return message[3]
			if message[0] == 2:
				self.waiting.append(message)

	def next_notification(self):
		"""The next notification, as (method, params), waiting for it if need be."""
		if self.waiting:
			message = self.waiting.pop(0)
			return message[1], message[2]
		for message in self._messages():
			if message[0] == 2:
				return message[1], message[2]

	def close(self):
		self.socket.close()

def main(argv=None):
	parser = argparse.ArgumentParser(description="Subscribe to a pycrocosm edit notification server and print what it sends.")
	parser.add_argument("host")
	parser.add_argument("port", type=int)
	parser.add_argument("--bbox", metavar="WEST,SOUTH,EAST,NORTH", help="Only edits that may concern this area")
	options = parser.parse_args(argv)
	client = NotifyClient(options.host, options.port)
	try:
		print("hello:", client.call("hello", {"protocol": 1, "client": "notify/client.py"}))
		subscription = {"bbox": [float(v) for v in options.bbox.split(",")]} if options.bbox else {}
		print("subscribe:", client.call("subscribe", subscription))
		while True:
			method, params = client.next_notification()
			print("{}: {}".format(method, params[0] if params else ""))
			sys.stdout.flush()
	except KeyboardInterrupt:
		pass
	except (ConnectionError, RuntimeError) as err:
		print(err, file=sys.stderr)
		return 1
	finally:
		client.close()
	return 0

if __name__ == "__main__":
	sys.exit(main())
