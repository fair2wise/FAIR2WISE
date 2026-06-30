import type { ReactNode } from 'react';
import { AlertCircle } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';

export function AppErrorMessage({
  title,
  children,
  className = '',
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Alert
      variant="destructive"
      className={`border-destructive/30 bg-destructive/5 ${className}`.trim()}
    >
      <AlertCircle aria-hidden="true" />
      {title && <AlertTitle>{title}</AlertTitle>}
      <AlertDescription className="text-destructive">{children}</AlertDescription>
    </Alert>
  );
}
