/** Подсказка заголовка абзацами: строка массива — абзац. */

import { Stack } from '@mantine/core';

export function HintLines({ lines }: { lines: readonly string[] }) {
  return (
    <Stack gap={6}>
      {lines.map((line) => (
        <span key={line}>{line}</span>
      ))}
    </Stack>
  );
}
