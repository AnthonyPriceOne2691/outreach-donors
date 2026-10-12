/**
 * Причина вердикта словами экрана. С 12.10.2026 сервер хранит её и у «не проверен»
 * («страны не получены» доберёт следующий прогон, «Ahrefs не знает домен» — нет), и там
 * у неё своя подпись: «Причина отсева» у домена, которого никто не отсеивал, читалась
 * бы приговором. Одна подпись на карточку, список доноров и «Отбор»; в выгрузке — своя
 * колонка (`donors/export.py`).
 */

import type { DonorStatus } from '../api/types';

/** Подпись причины: у «не проверен» — своя. */
export function reasonLabel(status: DonorStatus): string {
  return status === 'unchecked' ? 'Почему не проверен' : 'Причина отсева';
}

/** Причина строкой под доменом: у отказа — как есть, у «не проверен» — с подписью. */
export function reasonText(status: DonorStatus, reason: string): string {
  return status === 'unchecked' ? `${reasonLabel(status)}: ${reason}` : reason;
}
