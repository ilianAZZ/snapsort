/** The server's view of the world: is there a session, what sources are around.
 *
 * Kept in one place and refreshed on demand, because the answer changes while
 * the app runs — starting a session turns `session` from null into an object,
 * and the router's guards need the current answer, not the one from page load.
 */

import { api } from './api.js';

export const Boot = {
  data: { session: null, suggestions: [], autostart: false },

  async refresh() {
    this.data = await api('/api/bootstrap');
    return this.data;
  },

  get session() {
    return this.data.session;
  },
};
