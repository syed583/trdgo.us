import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Search } from 'lucide-react';

// ISO2 -> flag emoji (regional indicator letters), so we only store codes.
const flag = (iso: string) =>
  iso.toUpperCase().replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt(0)));

// name + ISO2 + dial code. Dial is the value stored by the form.
export const COUNTRIES: { iso: string; name: string; dial: string }[] = [
  { iso: 'AE', name: 'United Arab Emirates', dial: '971' },
  { iso: 'IN', name: 'India', dial: '91' },
  { iso: 'US', name: 'United States', dial: '1' },
  { iso: 'GB', name: 'United Kingdom', dial: '44' },
  { iso: 'CA', name: 'Canada', dial: '1' },
  { iso: 'SA', name: 'Saudi Arabia', dial: '966' },
  { iso: 'QA', name: 'Qatar', dial: '974' },
  { iso: 'KW', name: 'Kuwait', dial: '965' },
  { iso: 'BH', name: 'Bahrain', dial: '973' },
  { iso: 'OM', name: 'Oman', dial: '968' },
  { iso: 'PK', name: 'Pakistan', dial: '92' },
  { iso: 'BD', name: 'Bangladesh', dial: '880' },
  { iso: 'LK', name: 'Sri Lanka', dial: '94' },
  { iso: 'NP', name: 'Nepal', dial: '977' },
  { iso: 'SG', name: 'Singapore', dial: '65' },
  { iso: 'MY', name: 'Malaysia', dial: '60' },
  { iso: 'ID', name: 'Indonesia', dial: '62' },
  { iso: 'PH', name: 'Philippines', dial: '63' },
  { iso: 'TH', name: 'Thailand', dial: '66' },
  { iso: 'VN', name: 'Vietnam', dial: '84' },
  { iso: 'AU', name: 'Australia', dial: '61' },
  { iso: 'NZ', name: 'New Zealand', dial: '64' },
  { iso: 'DE', name: 'Germany', dial: '49' },
  { iso: 'FR', name: 'France', dial: '33' },
  { iso: 'ES', name: 'Spain', dial: '34' },
  { iso: 'IT', name: 'Italy', dial: '39' },
  { iso: 'NL', name: 'Netherlands', dial: '31' },
  { iso: 'BE', name: 'Belgium', dial: '32' },
  { iso: 'CH', name: 'Switzerland', dial: '41' },
  { iso: 'AT', name: 'Austria', dial: '43' },
  { iso: 'SE', name: 'Sweden', dial: '46' },
  { iso: 'NO', name: 'Norway', dial: '47' },
  { iso: 'DK', name: 'Denmark', dial: '45' },
  { iso: 'FI', name: 'Finland', dial: '358' },
  { iso: 'IE', name: 'Ireland', dial: '353' },
  { iso: 'PT', name: 'Portugal', dial: '351' },
  { iso: 'PL', name: 'Poland', dial: '48' },
  { iso: 'GR', name: 'Greece', dial: '30' },
  { iso: 'CZ', name: 'Czechia', dial: '420' },
  { iso: 'RO', name: 'Romania', dial: '40' },
  { iso: 'HU', name: 'Hungary', dial: '36' },
  { iso: 'RU', name: 'Russia', dial: '7' },
  { iso: 'UA', name: 'Ukraine', dial: '380' },
  { iso: 'TR', name: 'Turkey', dial: '90' },
  { iso: 'IL', name: 'Israel', dial: '972' },
  { iso: 'EG', name: 'Egypt', dial: '20' },
  { iso: 'ZA', name: 'South Africa', dial: '27' },
  { iso: 'NG', name: 'Nigeria', dial: '234' },
  { iso: 'KE', name: 'Kenya', dial: '254' },
  { iso: 'GH', name: 'Ghana', dial: '233' },
  { iso: 'TZ', name: 'Tanzania', dial: '255' },
  { iso: 'MA', name: 'Morocco', dial: '212' },
  { iso: 'DZ', name: 'Algeria', dial: '213' },
  { iso: 'JO', name: 'Jordan', dial: '962' },
  { iso: 'LB', name: 'Lebanon', dial: '961' },
  { iso: 'IQ', name: 'Iraq', dial: '964' },
  { iso: 'BR', name: 'Brazil', dial: '55' },
  { iso: 'MX', name: 'Mexico', dial: '52' },
  { iso: 'AR', name: 'Argentina', dial: '54' },
  { iso: 'CL', name: 'Chile', dial: '56' },
  { iso: 'CO', name: 'Colombia', dial: '57' },
  { iso: 'PE', name: 'Peru', dial: '51' },
  { iso: 'JP', name: 'Japan', dial: '81' },
  { iso: 'KR', name: 'South Korea', dial: '82' },
  { iso: 'CN', name: 'China', dial: '86' },
  { iso: 'HK', name: 'Hong Kong', dial: '852' },
  { iso: 'TW', name: 'Taiwan', dial: '886' },
];

export default function CountrySelect({ dial, setDial }:
  { dial: string; setDial: (d: string) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const sel = COUNTRIES.find((c) => c.dial === dial) || COUNTRIES[0];
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return COUNTRIES;
    return COUNTRIES.filter((c) =>
      c.name.toLowerCase().includes(s) || c.dial.includes(s) || c.iso.toLowerCase() === s);
  }, [q]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    setTimeout(() => searchRef.current?.focus(), 0);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const pick = (d: string) => { setDial(d); setOpen(false); setQ(''); };

  return (
    <div className="cs" ref={ref}>
      <button type="button" className="lp-dial cs-btn" aria-label="Country code"
        aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="cs-flag">{flag(sel.iso)}</span>
        <span className="cs-code">+{sel.dial}</span>
        <ChevronDown size={14} className="cs-chev" />
      </button>
      {open && (
        <div className="cs-pop" role="listbox">
          <div className="cs-search">
            <Search size={14} />
            <input ref={searchRef} value={q} placeholder="Search country or code"
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && filtered[0]) pick(filtered[0].dial);
              }} />
          </div>
          <ul className="cs-list">
            {filtered.map((c) => (
              <li key={c.iso} role="option" aria-selected={c.dial === dial}
                className={c.dial === dial ? 'on' : ''}
                onMouseDown={(e) => { e.preventDefault(); pick(c.dial); }}>
                <span className="cs-flag">{flag(c.iso)}</span>
                <span className="cs-name">{c.name}</span>
                <span className="cs-dial">+{c.dial}</span>
              </li>
            ))}
            {!filtered.length && <li className="cs-none">No match</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
