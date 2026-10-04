/**
 * «Сохранить новой версией» — у настроек, которые версионируются (пороги,
 * агент переписки): сохранение заводит версию, а не правит прежнюю, и кнопка
 * говорит это словами. Одна на все такие экраны — вид и слова не расходятся.
 */

import { Button } from '@mantine/core';
import { IconDeviceFloppy } from '@tabler/icons-react';

interface SaveVersionProps {
  busy: boolean;
  disabled: boolean;
  onSave: () => void;
}

export function SaveVersionButton({ busy, disabled, onSave }: SaveVersionProps) {
  return (
    <Button
      className="press"
      variant="gradient"
      leftSection={<IconDeviceFloppy size={18} />}
      loading={busy}
      disabled={disabled}
      onClick={onSave}
    >
      Сохранить новой версией
    </Button>
  );
}
