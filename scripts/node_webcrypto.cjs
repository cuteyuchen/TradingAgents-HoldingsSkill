// Keep local acceptance runs compatible with Node versions that do not expose
// Web Crypto globals by default.  Modern Node versions already have this API.
const nodeCrypto = require('node:crypto')
const { webcrypto } = nodeCrypto

if (typeof globalThis.crypto?.getRandomValues !== 'function') {
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    enumerable: false,
    value: webcrypto,
    writable: false,
  })
}

if (typeof nodeCrypto.getRandomValues !== 'function') {
  nodeCrypto.getRandomValues = webcrypto.getRandomValues.bind(webcrypto)
}
