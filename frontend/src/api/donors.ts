/**
 * Выгрузка доноров файлом.
 *
 * Выгружается то, что видно: фильтр — часть вопроса, на который отвечают
 * файлом, и выгрузка «всего» при включённом фильтре не совпала бы с экраном.
 * Страница и её размер при этом не передаются — файл читают не глазами,
 * и потолок строк у него свой, серверный.
 */

import { download } from './client';
import type { Downloaded } from './client';
import type { DonorQuery } from './runs';

export type DonorFilterQuery = Omit<DonorQuery, 'limit' | 'offset'>;

export function exportDonors(query: DonorFilterQuery): Promise<Downloaded> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value));
  }
  const tail = params.toString();
  return download(`/donors/export${tail === '' ? '' : `?${tail}`}`);
}

/** Через сколько убрать ссылку на файл. Не сразу: часть браузеров начинает
 *  скачивание после щелчка, а не в нём, и убранная тут же ссылка отменяла бы
 *  его молча. Секунды хватает с запасом, а памяти это почти не стоит. */
export const REVOKE_AFTER_MS = 1000;

/**
 * Отдать файл браузеру: временная ссылка на тело в памяти, щелчок, уборка.
 *
 * Ссылка на объект живёт до `revokeObjectURL`, и без уборки каждая выгрузка
 * держала бы файл в памяти вкладки до её закрытия.
 */
export function saveFile({ blob, filename }: Downloaded, fallback: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename ?? fallback;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
}
