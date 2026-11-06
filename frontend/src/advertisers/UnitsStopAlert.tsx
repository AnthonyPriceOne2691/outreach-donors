/**
 * Обходы стоят без юнитов Ahrefs — плашкой над всем разделом «Рекламодатели» (слово Anthony
 * 06.11.2026: «если юнитов нет, стопаем всё, и с понятным сообщением»).
 *
 * Над разделом, а не только на вкладке обхода: новых кандидатов не будет ни в «Проверке», ни на
 * панели, и человек должен видеть почему, откуда бы ни зашёл. Слова — сервера: сколько свободно, во
 * что упирается, что стоит, что идёт и когда снимется (`crawl/units_stop.py`).
 */

import { Alert } from '@mantine/core';

import { useUnitsStop } from './crawlQueries';

export function UnitsStopAlert() {
  const stop = useUnitsStop();
  if (stop === null) return null;
  return (
    <Alert color="red" title={stop.title}>
      {stop.detail}
    </Alert>
  );
}
