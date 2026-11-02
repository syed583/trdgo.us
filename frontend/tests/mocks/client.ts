// Test double for api/client: every call returns a marker for the mocked useApi; nothing here can reach a network.
export const AUTH_ERROR = 'AUTH_ERROR';
export const MAINTENANCE_ERROR = 'MAINTENANCE_ERROR';
export type ProviderStatus = string;
export const smartMoveQuality = {
  summary: () => ({ __k: 'summary' }),
  events: () => ({ __k: 'events' }),
};
export const smartMoveNewsReview = {
  queue: () => ({ __k: 'queue' }),
  label: async () => ({ status: 'RECORDED' }),
};
