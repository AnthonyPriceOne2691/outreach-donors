/**
 * Выгрузка раздела файлом: сохраняет файл и говорит, что в него легло. Числа — из заголовков
 * ответа, а не догадка: файл не обещает больше, чем в нём есть.
 */

import { useMutation } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import type { Downloaded } from '../api/client';
import { exportCounts, saveFile } from '../api/donors';
import { notify } from '../notices';

export function useFileExport(
  load: () => Promise<Downloaded>,
  fileName: string,
  exported: (rows: number | null, asked: number | null) => string,
) {
  return useMutation({
    mutationFn: load,
    onSuccess: (file) => {
      saveFile(file, fileName);
      const counts = exportCounts(file);
      notify({ message: exported(counts.rows, counts.asked) });
    },
    onError: (failure) =>
      notify({ title: 'Выгрузка не удалась', message: refusalOf(failure), color: 'red' }),
  });
}
