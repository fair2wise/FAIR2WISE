import mermaid from "https://unpkg.com/mermaid@10.4.0/dist/mermaid.esm.min.mjs";

mermaid.initialize({ startOnLoad: false });

if (typeof document$ !== "undefined") {
  document$.subscribe(() => {
    mermaid.run({ querySelector: ".mermaid" });
  });
} else {
  window.addEventListener("DOMContentLoaded", () => {
    mermaid.run({ querySelector: ".mermaid" });
  });
}
