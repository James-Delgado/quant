/**
 * Explanations — the Reference panel (E1-M5). Plain-language references for the
 * methods behind the numbers, also surfaced as inline ⓘ tips across the console
 * (DECISIONS #9). Content is STATIC and authored here: there is no
 * explanations.json in the export. Reading cards use the serif face
 * (`.panel.read`, --serif / IBM Plex Serif — DECISIONS #4, serif for the reading
 * view). Copy is ported verbatim from the frozen mockup
 * (docs/project-e/mockups/research-trust-console.html, data-v="explain").
 */

interface ReadingCard {
  title: string;
  body: string;
}

const CARDS: ReadingCard[] = [
  {
    title: "Purge & embargo",
    body: "Two leakage defenses: purge removes training labels that overlap the test window; embargo adds a gap for residual serial correlation, sized from how quickly autocorrelation decays.",
  },
  {
    title: "The luck bar",
    body: "An out-of-sample Sharpe is only meaningful against the best you'd expect from the number of strategies tried. That bar rises with the count — it is why the trial registry is kept.",
  },
  {
    title: "Condition attribution",
    body: "Performance is read per market condition, not just on aggregate, so a single favorable era cannot carry a verdict.",
  },
  {
    title: "Sample weighting",
    body: "Overlapping forward-return labels are not independent; each is weighted by the unique portion of its window.",
  },
  {
    title: "Equal-loss test",
    body: "Compares two forecasts' accuracy directly. A decisive result means one model's errors are smaller across the board.",
  },
  {
    title: "In-sample vs out-of-sample",
    body: "What a model leans on in training does not predict what helps out-of-sample here — so only out-of-sample evidence drives feature decisions.",
  },
];

export function Explanations() {
  return (
    <section>
      <div className="h1">Explanations</div>
      <div className="lead">
        Plain-language references for the methods behind the numbers — also
        surfaced as inline tips across the console.
      </div>
      <div className="grid c3" style={{ marginTop: 20 }}>
        {CARDS.map((c) => (
          <div className="panel read" key={c.title}>
            <h4>{c.title}</h4>
            <p>{c.body}</p>
            <span className="more" aria-hidden="true">
              read →
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
