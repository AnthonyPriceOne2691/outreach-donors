/**
 * «Отмена» и «Сохранить» окна правки — и почему «Сохранить» заперта, строкой под кнопками.
 *
 * Строкой под кнопками, а не в одном ряду с ними (правило 10.10.2026): в ряд окна 440 px они не
 * вставали, и «Сохранить» уезжала на вторую строку (снимок стенда). Общий кусок окон капа Ahrefs
 * и потолков модели на «Расходе»: две копии разошлись бы на первой правке.
 */

import { Button, Group, Stack, Text } from '@mantine/core';

import { ACTIONS_GAP } from './formRhythm';

interface Props {
  onCancel: () => void;
  saving: boolean;
  /** Почему «Сохранить» заперта — словами; `null` — открыта. */
  locked: string | null;
  /** Заперта без строки: причина уже названа у поля. */
  disabled?: boolean;
}

export function SaveActions({ onCancel, saving, locked, disabled = false }: Props) {
  return (
    <Stack gap={6} mt={ACTIONS_GAP}>
      <Group justify="flex-end" gap="sm">
        <Button variant="default" className="press" onClick={onCancel}>
          Отмена
        </Button>
        <Button
          type="submit"
          className="press"
          loading={saving}
          disabled={disabled || locked !== null}
        >
          Сохранить
        </Button>
      </Group>
      {locked !== null && (
        <Text size="sm" c="dimmed" ta="right">
          {locked}
        </Text>
      )}
    </Stack>
  );
}
