import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

export function CodeBlock({ content, cursor = false }: { content: string; cursor?: boolean }) {
  const [copied, setCopied] = useState(false);

  function copyCode() {
    navigator.clipboard.writeText(content).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }).catch(() => {
      // Clipboard unavailable — fail silently.
    });
  }

  return (
    <div className="overflow-hidden rounded-lg bg-slate-950">
      {!cursor && (
        <div className="flex items-center justify-end border-b border-slate-800 px-2 py-1">
          <button
            type="button"
            aria-label={copied ? 'Copied' : 'Copy code'}
            title={copied ? 'Copied' : 'Copy code'}
            onClick={copyCode}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-800 hover:text-slate-200"
          >
            {copied ? (
              <Check size={14} className="text-emerald-400" />
            ) : (
              <Copy size={14} />
            )}
          </button>
        </div>
      )}
      <pre className="overflow-x-auto px-3 py-2 text-xs text-slate-100">
        <code>
          {content}
          {cursor && <span className="ml-px animate-pulse text-slate-300">▌</span>}
        </code>
      </pre>
    </div>
  );
}
