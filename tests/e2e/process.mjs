import { setTimeout as delay } from 'node:timers/promises';

export function trackProcess(child) {
  let failure;
  const finished = new Promise(resolve => {
    child.once('error', error => { failure = error; resolve({ error }); });
    child.once('exit', (code, signal) => resolve({ code, signal }));
  });
  return {
    get error() { return failure; },
    async stop(timeout = 5000) {
      if (failure || child.exitCode !== null || child.signalCode !== null) return finished;
      child.kill('SIGTERM');
      const graceful = await Promise.race([finished, delay(timeout, null)]);
      if (graceful) return graceful;
      child.kill('SIGKILL');
      const killed = await Promise.race([finished, delay(timeout, null)]);
      if (!killed) throw new Error(`进程 ${child.pid} 未能在限定时间内退出`);
      return killed;
    },
  };
}
