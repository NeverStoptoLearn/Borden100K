import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
export function loopbackProxy(target) {
  if (!target) return undefined;
  const url = new URL(target);
  if (
    url.protocol !== 'http:' ||
    !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash
  )
    throw Error('BORDEN_API_PROXY must be an explicit loopback HTTP origin');
  return { '/api': { target: url.origin, changeOrigin: false, ws: true } };
}
export default defineConfig({
  plugins: [vue()],
  server: { proxy: loopbackProxy(process.env.BORDEN_API_PROXY) },
  test: { include: ['tests/**/*.test.js'] },
});
