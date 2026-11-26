import { ml_kem768 } from '@noble/post-quantum/ml-kem.js';
const api = Object.freeze({ version: '0.7.1', keygen: ml_kem768.keygen, encapsulate: ml_kem768.encapsulate, decapsulate: ml_kem768.decapsulate, lengths: ml_kem768.lengths });
Object.defineProperty(globalThis, 'NovashareMLKEM', { value: api, writable: false, configurable: false });
