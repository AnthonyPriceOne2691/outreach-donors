/**
 * Все спорные файлом — специалисту: пройтись по ним и дать обратную связь (тест-группа 31.10.2026).
 * В файле и решённые, с пометкой и с тем, кто решил — человек или судья, — и две пустые колонки под
 * ответ. Что легло — уведомлением по заголовкам ответа, как у выгрузки списка: файл не обещает
 * больше, чем в нём есть.
 */

import { Button } from '@mantine/core';

import { exportPending } from '../api/advertiserPanel';
import { formatNumber } from '../format';
import { useFileExport } from './useFileExport';

/** Что легло в файл — словами: потолок сервера говорит о себе сам. */
function exported(rows: number | null, asked: number | null): string {
  if (rows === null) return 'Файл выгружен.';
  if (asked === null || rows >= asked) return `Выгружено спорных: ${formatNumber(rows)}.`;
  return `В файле ${formatNumber(rows)} из ${formatNumber(asked)}: больше за раз не выгружается.`;
}

export function PendingExport({ total }: { total: number }) {
  const save = useFileExport(exportPending, 'disputed.csv', exported);
  return (
    <Button
      variant="default"
      size="xs"
      className="press"
      loading={save.isPending}
      disabled={total === 0}
      onClick={() => save.mutate()}
    >
      Выгрузить спорные · {formatNumber(total)}
    </Button>
  );
}
