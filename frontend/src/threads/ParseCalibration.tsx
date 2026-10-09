/**
 * Калибровка разбора ответов: что предложила модель против того, что
 * решил человек в очереди цены, — по текущей версии промпта.
 *
 * Приём соседней системы, работавший в бою: там по такой статистике
 * правили поведение агента в переписке. Здесь — разбор условий: какие
 * поля человек правит чаще, тем и занимается следующая версия промпта.
 *
 * Показывается только то, где смотрел человек. Цена, легшая в базу сама,
 * сверки не имеет — это отдельное число, а не «точность».
 */

import { Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { fetchCalibration } from '../api/outreach';

const FIELD_TITLES: Record<string, string> = {
  price_white: 'белая цена',
  price_grey: 'серая цена',
  currency: 'валюта',
  placement: 'продаёт ли',
};

/** Версия промпта словами — номером, как у версий порогов и настроек агента.
 *  Сервер называет её кодом (`reply-parse-v6-offers`): по коду версию ищут
 *  в журнале и в истории промпта, а человеку из него нужен только номер. */
export function versionTitle(code: string): string {
  const number = /^reply-parse-v(\d+)(?:-|$)/.exec(code)?.[1];
  return number === undefined ? 'версия без номера' : `версия ${number}`;
}

/** `inherit` — цветом подсказки, в которой стоит строка (`ThreadsTitle`): приглушённый
 *  тон на плотном стекле подсказки был бы на ступень бледнее её текста. */
export function ParseCalibration({ inherit = false }: { inherit?: boolean }) {
  const { data } = useQuery({ queryKey: ['replies-calibration'], queryFn: fetchCalibration });
  const current = data?.versions[0];
  if (current === undefined) return null;

  const fixes = Object.entries(current.wrong)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([field, count]) => `${FIELD_TITLES[field] ?? field} ${count}`);

  return (
    <Text size="sm" c={inherit ? 'inherit' : 'dimmed'}>
      Разбор ответов ({versionTitle(current.version)}): человек подтвердил как есть {current.as_is}{' '}
      из {current.reviewed}, поправил {current.edited}
      {fixes.length > 0 ? ` — чаще всего ${fixes.join(', ')}` : ''}. Сами легли в базу{' '}
      {current.auto_stored}, ждут человека {current.waiting}.
    </Text>
  );
}
