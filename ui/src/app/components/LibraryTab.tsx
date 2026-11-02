import { Library } from 'lucide-react';

/**
 * Library tab placeholder (A8, T40). T41 replaces the body with the papers table, upload dropzone,
 * Verify and Index actions. Exists now so the route, probe and keep-alive wiring can be tested.
 */
export function LibraryTab() {
  return (
    <section
      aria-labelledby="library-tab-title"
      className="flex h-full w-full flex-col items-center justify-center gap-2 rounded-xl border border-border bg-card p-6 text-card-foreground"
    >
      <Library aria-hidden="true" size={32} />
      <h2 id="library-tab-title" className="text-lg font-semibold">
        Library
      </h2>
      <p className="text-sm text-muted-foreground">Reference papers for extraction and QA.</p>
    </section>
  );
}
