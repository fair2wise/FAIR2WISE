import { ButtonWithIcon } from '@blueskyproject/finch';
import { SquarePen } from 'lucide-react';

export function AppNewChatButton({ onClick }: { onClick: () => void }) {
  return (
    <ButtonWithIcon
      text="New chat"
      icon={<SquarePen size={16} strokeWidth={2} aria-hidden="true" />}
      isSecondary
      size="small"
      aria-label="New chat"
      onClick={onClick}
    />
  );
}
