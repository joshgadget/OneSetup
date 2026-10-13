import { useMemo, useState } from "react";
import type { SymbolInfo } from "../api";

const GROUP_ORDER = [
  "forex",
  "gold",
  "commodity",
  "index",
  "crypto_perp",
  "crypto_futures",
  "crypto_spot",
  "synthetic",
];

const GROUP_LABEL: Record<string, string> = {
  forex: "Forex",
  gold: "Gold",
  commodity: "Commodities",
  index: "Indices",
  crypto_perp: "Crypto perpetuals",
  crypto_futures: "Crypto futures",
  crypto_spot: "Crypto spot",
  synthetic: "Synthetic indices",
};

interface Props {
  symbols: SymbolInfo[];
  current: string;
  favourites: string[];
  onSelect: (canonicalId: string) => void;
  onToggleFavourite: (canonicalId: string) => void;
  showAll: boolean;
  onShowAll: (value: boolean) => void;
}

export default function Watchlist({
  symbols,
  current,
  favourites,
  onSelect,
  onToggleFavourite,
  showAll,
  onShowAll,
}: Props) {
  const [filter, setFilter] = useState("");

  const rows = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return symbols.filter((s) => {
      if (!showAll && !s.has_data && s.canonical_id !== current) return false;
      if (!needle) return true;
      return (
        s.canonical_id.toLowerCase().includes(needle) ||
        s.display.toLowerCase().includes(needle) ||
        s.venue.toLowerCase().includes(needle)
      );
    });
  }, [symbols, filter, showAll, current]);

  const groups = useMemo(() => {
    const byClass = new Map<string, SymbolInfo[]>();
    for (const row of rows) {
      const list = byClass.get(row.market_class) ?? [];
      list.push(row);
      byClass.set(row.market_class, list);
    }
    return [...byClass.entries()].sort(
      (a, b) =>
        (GROUP_ORDER.indexOf(a[0]) + 1 || 99) - (GROUP_ORDER.indexOf(b[0]) + 1 || 99) ||
        a[0].localeCompare(b[0])
    );
  }, [rows]);

  const favouriteRows = useMemo(
    () => favourites.map((id) => symbols.find((s) => s.canonical_id === id)).filter(Boolean) as SymbolInfo[],
    [favourites, symbols]
  );

  const row = (info: SymbolInfo) => (
    <li key={info.canonical_id} className={info.canonical_id === current ? "active" : ""}>
      <button
        className="star"
        title={favourites.includes(info.canonical_id) ? "remove from favourites" : "add to favourites"}
        onClick={() => onToggleFavourite(info.canonical_id)}
      >
        {favourites.includes(info.canonical_id) ? "*" : "+"}
      </button>
      <button className="row" onClick={() => onSelect(info.canonical_id)}>
        <span className={`dot ${info.has_data ? "has" : ""}`} title={info.has_data ? "candles stored" : "no candles yet"} />
        <span className="id">{info.canonical_id}</span>
        <span className="venue">{info.venue}</span>
      </button>
    </li>
  );

  return (
    <aside className="watchlist">
      <div className="watchlist-head">
        <h2>Symbols</h2>
        <span className="notes">{rows.length} shown</span>
      </div>
      <input
        className="search"
        placeholder="filter, e.g. BTC or R_"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <label className="showall">
        <input type="checkbox" checked={showAll} onChange={(e) => onShowAll(e.target.checked)} />
        include symbols without stored candles
      </label>
      <div className="watchlist-body">
        {favouriteRows.length > 0 && (
          <>
            <div className="group">Favourites</div>
            <ul>{favouriteRows.map(row)}</ul>
          </>
        )}
        {groups.map(([marketClass, list]) => (
          <div key={marketClass}>
            <div className="group">
              {GROUP_LABEL[marketClass] ?? marketClass} <span className="notes">{list.length}</span>
            </div>
            <ul>{list.slice(0, 300).map(row)}</ul>
            {list.length > 300 && (
              <p className="notes">showing the first 300 of {list.length}; filter to narrow</p>
            )}
          </div>
        ))}
      </div>
    </aside>
  );
}
