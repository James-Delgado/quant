import { NAV_ITEMS } from "@/nav";

/**
 * Scaffold-only panel body (E1-M2). Renders the panel's real heading + lead so
 * the shell reads correctly, plus an honest "arrives in a later milestone"
 * empty state. No data is shown yet — the Monitor panels land in E1-M3 and the
 * Evidence panels in E1-M4, each replacing this placeholder with a real page.
 */

// Lead copy is lifted from the mockup so the headings already read true.
const LEADS: Record<string, { lead: string; milestone: string }> = {
  overview: {
    lead: "Portfolio performance, which strategies are up or down and why, and data + market status at a glance.",
    milestone: "E1-M3",
  },
  strategies: {
    lead: "Every research strategy under evaluation, each benchmarked against the ARIMA(1,0,0) control — roster to detail.",
    milestone: "E1-M3",
  },
  conditions: {
    lead: "Where each strategy earns or bleeds, by live-computable market condition, with named episodes kept as stress windows.",
    milestone: "E1-M3",
  },
  data: {
    lead: "Feed health over the lake and a snapshot of the market environment the strategies operate in.",
    milestone: "E1-M4",
  },
  provenance: {
    lead: "The exact inputs and controls behind a result — reproducible from the pinned configuration and commit.",
    milestone: "E1-M4",
  },
  catalog: {
    lead: "The registered feature set with live monitoring — summary statistics, coverage, and a drift check.",
    milestone: "E1-M4",
  },
  ledger: {
    lead: "Every pre-registered comparison and its verdict, driving the multiple-testing bar.",
    milestone: "E1-M4",
  },
  explain: {
    lead: "Plain-language references for the methods behind the numbers.",
    milestone: "E1-M5",
  },
};

export function Placeholder({ slug }: { slug: string }) {
  const item = NAV_ITEMS.find((i) => i.path === slug);
  const meta = LEADS[slug];
  return (
    <section>
      <div className="h1">{item?.title ?? "Panel"}</div>
      {meta && <div className="lead">{meta.lead}</div>}
      <div className="ph">
        <span className="pill warn">
          <i aria-hidden="true" /> scaffold
        </span>
        <div>
          This panel is rendered by the app shell. Its content lands in{" "}
          <strong>{meta?.milestone ?? "a later milestone"}</strong>, fed by the
          console export through the static data client.
        </div>
      </div>
    </section>
  );
}
