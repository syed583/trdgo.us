// B.2a: these three board configs used to live in IntradayBoardPage and were imported STATICALLY by
// App.tsx, which defeated its own lazy() and pulled that administrator-only page - model names and all -
// into the chunk every visitor downloads. They live here so the page can actually be split out.
import { api2 } from '../api/client';
import type { IntradayCfg } from './IntradayBoardPage';

export const META_INTRADAY_CFG: IntradayCfg = {
  title: 'Agent 2 Intraday', subtitle: 'Live Board',
  base: '/meta-intraday', board: (s) => api2.metaIntradayBoard(s),
};
export const KIMI_INTRADAY_CFG: IntradayCfg = {
  title: 'Agent 3 Intraday', subtitle: 'Live Board',
  base: '/kimi-intraday', board: (s) => api2.kimiIntradayBoard(s),
};
export const GEMINI_INTRADAY_CFG: IntradayCfg = {
  title: 'Agent 4 Intraday', subtitle: 'Live Board',
  base: '/gemini-intraday', board: (s) => api2.geminiIntradayBoard(s),
};
