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

export function ParseCalibration() {
  const { data } = useQuery({ queryKey: ['replies-calibration'], queryFn: fetchCalibration });
  const current = data?.versions[0];
  if (current === undefined) return null;

  const fixes = Object.entries(current.wrong)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([field, count]) => `${FIELD_TITLES[field] ?? field} ${count}`);

  return (
    <Text size="sm" c="dimmed">
      Разбор ответов ({current.version}): человек подтвердил как есть {current.as_is} из{' '}
      {current.reviewed}, поправил {current.edited}
      {fixes.length > 0 ? ` — чаще всего ${fixes.join(', ')}` : ''}. Сами легли в базу{' '}
      {current.auto_stored}, ждут человека {current.waiting}.
    </Text>
  );
}
