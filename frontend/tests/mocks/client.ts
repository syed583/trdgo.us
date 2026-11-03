// Test double for api/client: every call returns a marker for the mocked useApi; nothing here can reach a network.
export const AUTH_ERROR = 'AUTH_ERROR';
export const MAINTENANCE_ERROR = 'MAINTENANCE_ERROR';
export type ProviderStatus = string;
export const earningsMonitor = {
  status: () => ({ __k: 'emstatus' }),
  upcoming: () => ({ __k: 'emupcoming' }),
  models: () => ({ __k: 'emmodels' }),
  changes: () => ({ __k: 'emchanges' }),
  consensus: () => ({ __k: 'emconsensus' }),
  accuracy: () => ({ __k: 'emaccuracy' }),
  comparison: () => ({ __k: 'emcomparison' }),
  sources: () => ({ __k: 'emsources' }),
  history: () => ({ __k: 'emhistory' }),
  evidence: () => ({ __k: 'emevidence' }),
  smartMove: () => ({ __k: 'emsmart' }),
  health: () => ({ __k: 'emhealth' }),
  outcomes: () => ({ __k: 'emoutcomes' }),
};
