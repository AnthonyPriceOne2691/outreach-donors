/**
 * Слова резерва: группы причин, место страны прогона у домена, итог «Взять на
 * рассмотрение». Причину и отказ называет сервер — здесь только то, что экран собирает
 * из чисел.
 */

import { countryTitle } from '../api/labels';
import { formatNumber, formatShare, plural } from '../format';
import type { CountryFit, ReserveGroup, TakeResult } from './reserveApi';

/** Группа причины — подписью фильтра. */
export const GROUP_TITLES: Record<ReserveGroup, string> = {
  weak: 'Слабые метрики',
  region: 'Регион',
  gap: 'Не проверен — не хватает данных',
  not_writing: 'Кому не пишем',
};

/** Где страна прогона у домена — одной строкой: место и доля, или почему места нет. */
export function placeWords(fit: CountryFit, country: string): string {
  const name = countryTitle(country);
  if (!fit.asked) return 'страны не спрашивали';
  if (fit.place !== null) return `${name} — ${fit.place}-я · ${formatShare(fit.share)}`;
  if (fit.top === null) return 'данных по странам нет';
  if (fit.partial) return `${name} — не видно: одна верхняя страна`;
  return `${name} не в топ-5`;
}

/** Верхняя страна домена — второй строкой, если это не страна прогона. */
export function topWords(fit: CountryFit, country: string): string | null {
  if (fit.top === null || fit.place === 1) return null;
  const top = `верхняя — ${countryTitle(fit.top)} · ${formatShare(fit.top_share)}`;
  return fit.top === country.toLowerCase() ? null : top;
}

const domainsWord = (count: number) => plural(count, 'домен', 'домена', 'доменов');

/** Итог «Взять на рассмотрение» — уведомлением: сколько взято и в очереди каких прогонов,
 *  сколько уже лежало, сколько отказано (почему — у строки). */
export function takeOutcome(result: TakeResult): string {
  const runs = Object.keys(result.runs).map((id) => `№${id}`);
  const parts: string[] = [];
  if (result.taken > 0) {
    const where = runs.length === 1 ? `прогона ${runs[0]}` : `прогонов ${runs.join(', ')}`;
    parts.push(
      `Взято на рассмотрение: ${formatNumber(result.taken)} ${domainsWord(result.taken)} — в очереди ${where}`,
    );
  }
  if (result.already.length > 0) {
    parts.push(`Уже были в очереди: ${formatNumber(result.already.length)}`);
  }
  const refused = Object.entries(result.refused);
  if (refused.length > 0) {
    parts.push(`Не взяты: ${refused.map(([host, why]) => `${host} — ${why}`).join('; ')}`);
  }
  return parts.length === 0 ? 'Брать было нечего' : parts.join('. ');
}
