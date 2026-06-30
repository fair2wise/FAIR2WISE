import { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Brain, User, Atom } from 'lucide-react';
import { queryRag } from './data/mockRag';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  usedNodeIds?: string[];
  confidence?: number;
}

interface Props {
  messages: Message[];
  activeMessageId: string | null;
  onQuery: (query: string, nodeIds: string[], response: string, confidence: number) => void;
  onMessageClick: (msg: Message) => void;
}

function parseMarkdown(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i} style={{ color: 'rgba(255,255,255,0.95)', fontWeight: 600 }}>{part.slice(2, -2)}</strong>;
    }
    return <span key={i}>{part}</span>;
  });
}

function AssistantMessage({ msg, isActive, onClick }: { msg: Message; isActive: boolean; onClick: () => void }) {
  const paragraphs = msg.content.split('\n\n').filter(Boolean);

  return (
    <div
      className="flex gap-3 group cursor-pointer"
      onClick={onClick}
    >
      <div className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center mt-0.5" style={{ background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)' }}>
        <Brain size={13} style={{ color: 'white' }} />
      </div>
      <div className="flex-1 min-w-0">
        <div
          className="rounded-xl px-4 py-3 transition-all"
          style={{
            background: isActive ? 'rgba(59,130,246,0.08)' : 'rgba(255,255,255,0.04)',
            border: isActive ? '1px solid rgba(59,130,246,0.25)' : '1px solid rgba(255,255,255,0.06)',
          }}
        >
          {paragraphs.map((para, i) => (
            <p key={i} className="text-sm leading-relaxed" style={{ color: 'rgba(255,255,255,0.75)', marginBottom: i < paragraphs.length - 1 ? '0.75rem' : 0 }}>
              {parseMarkdown(para)}
            </p>
          ))}
        </div>
        {msg.usedNodeIds && (
          <div className="flex items-center gap-2 mt-1.5 px-1">
            <div className="flex items-center gap-1">
              <div className="w-1.5 h-1.5 rounded-full" style={{ background: '#10b981' }} />
              <span className="text-xs" style={{ color: 'rgba(255,255,255,0.3)' }}>
                {msg.usedNodeIds.length} nodes retrieved
              </span>
            </div>
            {msg.confidence !== undefined && (
              <>
                <span style={{ color: 'rgba(255,255,255,0.15)' }}>·</span>
                <span className="text-xs" style={{ color: 'rgba(255,255,255,0.3)' }}>
                  confidence {(msg.confidence * 100).toFixed(0)}%
                </span>
              </>
            )}
            <span className="text-xs ml-auto opacity-0 group-hover:opacity-100 transition-opacity" style={{ color: 'rgba(255,255,255,0.25)' }}>
              click to highlight graph
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

const SUGGESTIONS = [
  'What are the strongest lightweight materials?',
  'Which materials are best for aerospace applications?',
  'Compare electrical conductivity of copper and graphene',
  'How does heat treatment affect steel hardness?',
  'What materials are used in biomedical implants?',
  'Which ceramics have the highest thermal conductivity?',
];

export function ChatPanel({ messages, activeMessageId, onQuery, onMessageClick }: Props) {
  const [input, setInput] = useState('');
  const [isThinking, setIsThinking] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isThinking]);

  const submit = useCallback(async (query: string) => {
    const q = query.trim();
    if (!q || isThinking) return;
    setInput('');
    setIsThinking(true);

    // Simulate network latency
    await new Promise(r => setTimeout(r, 600 + Math.random() * 800));

    const result = queryRag(q);
    setIsThinking(false);
    onQuery(q, result.usedNodeIds, result.response, result.confidence);
  }, [isThinking, onQuery]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit(input);
    }
  };

  return (
    <div className="flex flex-col w-96 shrink-0 border-r" style={{ background: '#0a0b0f', borderColor: 'rgba(255,255,255,0.08)' }}>
      {/* Header */}
      <div className="px-4 py-3 border-b shrink-0" style={{ borderColor: 'rgba(255,255,255,0.08)' }}>
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)' }}>
            <Atom size={15} style={{ color: 'white' }} />
          </div>
          <div>
            <div className="text-sm" style={{ color: 'rgba(255,255,255,0.9)', fontWeight: 500 }}>MatSci Graph RAG</div>
            <div className="text-xs" style={{ color: 'rgba(255,255,255,0.35)' }}>Materials science assistant</div>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4 min-h-0">
        {messages.map(msg => {
          if (msg.role === 'user') {
            return (
              <div key={msg.id} className="flex gap-3 justify-end">
                <div className="max-w-[85%] rounded-xl px-4 py-2.5" style={{ background: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.2)' }}>
                  <p className="text-sm" style={{ color: 'rgba(255,255,255,0.85)' }}>{msg.content}</p>
                </div>
                <div className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center mt-0.5" style={{ background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)' }}>
                  <User size={13} style={{ color: 'rgba(255,255,255,0.5)' }} />
                </div>
              </div>
            );
          }
          return (
            <AssistantMessage
              key={msg.id}
              msg={msg}
              isActive={msg.id === activeMessageId}
              onClick={() => onMessageClick(msg)}
            />
          );
        })}

        {isThinking && (
          <div className="flex gap-3">
            <div className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)' }}>
              <Brain size={13} style={{ color: 'white' }} />
            </div>
            <div className="rounded-xl px-4 py-3" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' }}>
              <div className="flex items-center gap-1.5">
                <span className="text-xs" style={{ color: 'rgba(255,255,255,0.3)' }}>Traversing graph</span>
                <span className="flex gap-0.5 ml-1">
                  {[0, 1, 2].map(i => (
                    <span
                      key={i}
                      className="inline-block w-1 h-1 rounded-full"
                      style={{
                        background: '#3b82f6',
                        animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
                      }}
                    />
                  ))}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Suggestions (only on first load) */}
        {messages.length === 1 && !isThinking && (
          <div className="space-y-2 pt-2">
            <p className="text-xs" style={{ color: 'rgba(255,255,255,0.25)' }}>Try asking about:</p>
            {SUGGESTIONS.map((s, i) => (
              <button
                key={i}
                onClick={() => submit(s)}
                className="block w-full text-left text-xs px-3 py-2 rounded-lg transition-colors"
                style={{
                  color: 'rgba(255,255,255,0.5)',
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}
                onMouseEnter={e => {
                  (e.currentTarget as HTMLButtonElement).style.background = 'rgba(59,130,246,0.08)';
                  (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(59,130,246,0.2)';
                  (e.currentTarget as HTMLButtonElement).style.color = 'rgba(255,255,255,0.7)';
                }}
                onMouseLeave={e => {
                  (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.03)';
                  (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(255,255,255,0.06)';
                  (e.currentTarget as HTMLButtonElement).style.color = 'rgba(255,255,255,0.5)';
                }}
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t shrink-0" style={{ borderColor: 'rgba(255,255,255,0.08)' }}>
        <div
          className="flex items-end gap-2 rounded-xl px-3 py-2"
          style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)' }}
        >
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about materials science…"
            rows={1}
            disabled={isThinking}
            className="flex-1 resize-none bg-transparent outline-none text-sm leading-relaxed"
            style={{
              color: 'rgba(255,255,255,0.85)',
              maxHeight: '120px',
              overflowY: 'auto',
            }}
          />
          <button
            onClick={() => submit(input)}
            disabled={!input.trim() || isThinking}
            className="shrink-0 w-7 h-7 rounded-lg flex items-center justify-center transition-all"
            style={{
              background: input.trim() && !isThinking ? '#3b82f6' : 'rgba(255,255,255,0.06)',
              opacity: input.trim() && !isThinking ? 1 : 0.5,
            }}
          >
            <Send size={13} style={{ color: 'white' }} />
          </button>
        </div>
        <p className="text-xs mt-1.5 text-center" style={{ color: 'rgba(255,255,255,0.18)' }}>
          Enter to send · Shift+Enter for new line
        </p>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.3; transform: scale(0.8); }
          50% { opacity: 1; transform: scale(1.2); }
        }
      `}</style>
    </div>
  );
}
