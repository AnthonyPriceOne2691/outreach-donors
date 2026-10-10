/**
 * Смета прогона на экране: что будет куплено и во что обойдётся.
 *
 * Отдельно от экрана прогона (`RunPage`), который держит поля, запуск и историю:
 * смета — то, что сервер посчитал по этим полям, и числа здесь только называются,
 * а не считаются заново. Своих копий правил сметы у экрана нет: сколько ключей
 * купит выдача и какая доля доменов схлопнется в дубли, говорит ответ сервера.
 */

import { Alert, SimpleGrid, Stack, Text } from '@mantine/core';

import type { Forecast } from '../api/types';
import { Metric } from '../components/Metric';
import { belowCent, formatNumber, formatShare, formatUsd, plural } from '../format';
import { depthTitle, keywordsTitle, RESULTS_PER_PAGE } from './depth';

/** Ключи сметы — те, что купит выдача: сервер сводит повторы без учёта регистра
 *  и лишних пробелов (проверка прода 10.10.2026: два одинаковых ключа и вариант
 *  с заглавными были «3 ключа», а выдача покупала два). Строк в поле больше — это
 *  называется, иначе «2 ключа» под тремя строками читалось бы ошибкой сметы. */
function resultsHint(forecast: Forecast, lines: number): string {
  // Те же слова, что у поля: «глубина выдачи — 20 результатов».
  const asked = `${keywordsTitle(forecast.keywords)} × ${depthTitle(forecast.depth_pages * RESULTS_PER_PAGE)}`;
  const repeats = lines - forecast.keywords;
  return repeats > 0
    ? `${asked} · ${repeats} ${plural(repeats, 'повтор', 'повтора', 'повторов')} не в счёт`
    : asked;
}

interface Props {
  forecast: Forecast;
  /** Сколько непустых строк в поле ключей — смета посчитана по ним. */
  lines: number;
}

export function Estimate({ forecast, lines }: Props) {
  return (
    <Stack gap="sm">
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Metric
          title="Результатов выдачи"
          value={formatNumber(forecast.expected_results)}
          hint={resultsHint(forecast, lines)}
        />
        {/* Доля дублей — из доли, которой посчитана сама смета (сервер берёт её из
            истории прогонов): «83% — по замеру» стояло от константы, убранной 24.09,
            рядом с «≈ 31 из 40» (проверка прода 10.10.2026). */}
        <Metric
          title="Уникальных доменов"
          value={`≈ ${formatNumber(forecast.expected_domains)}`}
          hint={`${formatShare(1 - forecast.unique_share)} схлопывается в дубли — по замеру`}
        />
        <Metric
          title="Юнитов Ahrefs"
          value={`до ${formatNumber(forecast.units_total)}`}
          hint={`просев ${formatNumber(forecast.units_screen)} · метрики ${formatNumber(forecast.units_metrics)} · гео ${formatNumber(forecast.units_by_country)}`}
        />
        <Metric
          title="Бюджет прогона"
          value={formatNumber(forecast.budget)}
          hint={
            forecast.run_ceiling === null
              ? `у провайдера ${formatNumber(forecast.units_left)}, по капу ${formatNumber(forecast.cap_left)} из ${formatNumber(forecast.units_cap)}`
              : `ваш потолок ${formatNumber(forecast.run_ceiling)}; по капу ${formatNumber(forecast.cap_left)} из ${formatNumber(forecast.units_cap)}`
          }
        />
      </SimpleGrid>

      {/* Выдача платится деньгами, а не юнитами, и кнопку не блокирует:
          без неё прогона нет вовсе. Но названа она должна быть — до этой
          строки расход на выдачу не показывался нигде. Провайдер берёт за
          каждые десять результатов: сто результатов — вдесятеро дороже.
          Доли цента — «меньше 0,01 $», а не «примерно 0,00 $» (проверка прода
          10.10.2026): «примерно» при «меньше» лишнее. */}
      <Text size="sm" c="dimmed">
        Выдача будет стоить {belowCent(forecast.serp_cost_usd) ? '' : 'примерно '}
        <b>{formatUsd(forecast.serp_cost_usd)}</b> — это другой счёт, не юниты Ahrefs. Потрачено
        нами юнитов с начала месяца: <b>{formatNumber(forecast.units_spent_this_month)}</b>.
      </Text>

      {forecast.affordable ? (
        <Alert color="green" title="Помещается">
          Смета считает худший случай — что все домены новые. За те, у которых данные ещё свежие,
          второй раз не платят, поэтому по факту обычно меньше.
        </Alert>
      ) : (
        <Alert color="red" title="Не помещается в бюджет">
          Не хватает {formatNumber(forecast.shortfall)} юнитов. Сократите список ключей или глубину
          выдачи — либо поднимите кап, если остаток у провайдера позволяет.
        </Alert>
      )}
    </Stack>
  );
}
