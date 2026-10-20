'use strict';
// Fixture child processes may connect only to loopback or local Unix sockets.
const net = require('node:net');
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  const first = args[0];
  const options = Array.isArray(first) ? first[0] : first;
  let host;
  if (typeof options === 'object') host = options.host || options.hostname;
  else if (typeof options === 'number') host = typeof args[1] === 'string' ? args[1] : 'localhost';
  else if (typeof options === 'string' && !options.startsWith('/')) host = options;
  if (host && !['localhost','127.0.0.1','::1'].includes(host))
    throw new Error('Offline validation blocked an external connection');
  return connect.apply(this,args);
};
