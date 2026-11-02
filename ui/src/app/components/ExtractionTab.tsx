import { FlaskConical } from 'lucide-react';

/**
 * Extraction tab placeholder (A8, T40). T42 replaces the body with the running view (progress, live
 * log) and the idle view (continue or new KG, papers, model, schema, cost confirmation).
 */
export function ExtractionTab() {
  return (
    <section
      aria-labelledby="extraction-tab-title"
      className="flex h-full w-full flex-col items-center justify-center gap-2 rounded-xl border border-border bg-card p-6 text-card-foreground"
    >
      <FlaskConical aria-hidden="true" size={32} />
      <h2 id="extraction-tab-title" className="text-lg font-semibold">
        Extraction
      </h2>
      <p className="text-sm text-muted-foreground">Build or continue a knowledge graph from library papers.</p>
    </section>
  );
}
