import { useState } from 'react';
import type { PageContext } from '../App';
import OiBuildup from '../components/OiBuildup';
import { ContractModal } from '../components/ContractModal';

/**
 * OI Build-Up as its own screen: the overnight open-interest-change explorer,
 * market-wide and per-ticker, with rows that open the contract-detail modal.
 */
export default function OiBuildupPage({ ctx }: { ctx: PageContext }) {
  const { symbol, demo } = ctx;
  const [contract, setContract] = useState<string | null>(null);

  return (
    <>
      {contract && (
        <ContractModal occ={contract} onClose={() => setContract(null)} />
      )}
      <OiBuildup symbol={symbol} demo={demo} onContract={setContract} />
    </>
  );
}
