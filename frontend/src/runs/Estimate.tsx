/**
 * Смета прогона на экране: что будет куплено и во что обойдётся.
 *
 * Отдельно от экрана прогона (`RunPage`), который держит поля, запуск и историю:
 * смета — то, что сервер посчитал по этим полям, и числа здесь только называются,
 * а не считаются заново. Своих копий правил сметы у экрана нет: сколько ключей
 * купит выдача и какая доля доменов схлопнется в дубли, говорит ответ сервера.
 */

import { Alert, Anchor, SimpleGrid, Stack, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import { permissionTitle } from '../api/labels';
import type { Forecast } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { belowCent, formatDay, formatNumber, formatShare, formatUsd, plural } from '../format';
import { depthTitle, keywordsTitle, RESULTS_PER_PAGE } from './depth';
import { waitTitle } from './serpMode';

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

/** «Поднимите кап» — с адресом: кап меняют на экране «Расход» (решение владельца
 *  16.10.2026), и совет без места, где его исполнить, человеку не помогал. Кто меняет —
 *  словами права, а не роли: право «править пороги» по умолчанию есть и у оператора, и
 *  «меняет админ» отправляло бы оператора за тем, что он может сам. Ссылка — чернилами
 *  с подчёркиванием (`inkLink`), как ссылки посреди текста на главной. */
function RaiseCap({ free }: { free: number }) {
  const { can } = useSession();
  const usage = (
    <Anchor
      component={Link}
      to="/usage"
      size="sm"
      fw={500}
      c="var(--ink)"
      underline="always"
      className="inkLink"
    >
      «Расход»
    </Anchor>
  );
  return can('settings') ? (
    <>
      либо поднимите кап на экране {usage}: у Ahrefs свободно {formatNumber(free)}.
    </>
  ) : (
    <>
      либо попросите поднять кап на экране {usage}: у Ahrefs свободно {formatNumber(free)}, а кап
      меняет учётка с правом «{permissionTitle('settings')}».
    </>
  );
}

/** Подпись бюджета: все числа, из которых он взят, а то, во что упирается, — первым и
 *  словом «упор» (16.10.2026): меньшее из трёх читалось без подсказки, какое оно. */
function budgetHint(forecast: Forecast): string {
  const ahrefs = `у Ahrefs ${formatNumber(forecast.units_left)}`;
  const cap = capHint(forecast);
  const ceiling =
    forecast.run_ceiling === null ? null : `ваш потолок ${formatNumber(forecast.run_ceiling)}`;
  const bound = { key: ahrefs, workspace: ahrefs, cap, ceiling, none: null }[
    forecast.binding ?? 'none'
  ];
  const rest = [ahrefs, cap, ceiling].filter((part) => part !== null && part !== bound);
  return (bound === null ? rest : [`упор — ${bound}`, ...rest]).join(' · ');
}

/** Не помещается — во что упёрлись и что делать: у каждого упора свой совет (16.10.2026:
 *  «поднимите кап» при кончившемся ключе Ahrefs вёл поднимать кап зря). */
function ShortAlert({ forecast }: { forecast: Forecast }) {
  const short = `Не хватает ${formatNumber(forecast.shortfall)} юнитов`;
  const until = forecast.cycle_by_key ? ` до обнуления ${formatDay(forecast.next_reset)}` : '';
  const free = formatNumber(forecast.units_left);
  const trim = 'Пока сократите список ключей или глубину выдачи.';
  switch (forecast.binding) {
    case 'key':
      return (
        <Alert color="red" title="Не помещается: кончается лимит ключа Ahrefs">
          {short}: на ключе Ahrefs свободно {free}
          {until}. Кап здесь не поможет — лимит ключа поднимают в самом Ahrefs. {trim}
        </Alert>
      );
    case 'workspace':
      return (
        <Alert color="red" title="Не помещается: кончается лимит пространства Ahrefs">
          {short}: в пространстве Ahrefs свободно {free}
          {until} — это общий лимит всех его ключей. Кап здесь не поможет — лимит пространства
          поднимают в самом Ahrefs. {trim}
        </Alert>
      );
    case 'ceiling':
      return (
        <Alert color="red" title="Не помещается в ваш потолок">
          {short} до потолка этого прогона, {formatNumber(forecast.run_ceiling ?? 0)}. Поднимите или
          уберите потолок в форме — или сократите список ключей или глубину выдачи.
        </Alert>
      );
    default:
      return (
        <Alert color="red" title="Не помещается в кап">
          {short}: по нашему капу осталось {formatNumber(forecast.cap_left)} из{' '}
          {formatNumber(forecast.units_cap ?? 0)}
          {until}. Сократите список ключей или глубину выдачи —{' '}
          <RaiseCap free={forecast.units_left} />
        </Alert>
      );
  }
}

/** Кап — на цикл ключа Ahrefs (16.10.2026): «по капу N из M» от размера ключа; размер
 *  неизвестен — так и сказано, а не «по капу 0 из 0». */
function capHint(forecast: Forecast): string {
  return forecast.units_cap === null
    ? `кап неизвестен: ${forecast.cap_unknown ?? 'размер ключа Ahrefs неизвестен'}`
    : `по капу ${formatNumber(forecast.cap_left)} из ${formatNumber(forecast.units_cap)}`;
}

/** С какого дня считается наш расход: от обнуления ключа или — без его даты — с первого. */
function since(forecast: Forecast): string {
  return forecast.cycle_by_key
    ? `за цикл ключа, с ${formatDay(forecast.cycle_start)}`
    : `с ${formatDay(forecast.cycle_start)} — Ahrefs не назвал даты обнуления ключа`;
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
          hint={budgetHint(forecast)}
        />
      </SimpleGrid>

      {/* Выдача платится деньгами, а не юнитами, и кнопку не блокирует:
          без неё прогона нет вовсе. Но названа она должна быть — до этой
          строки расход на выдачу не показывался нигде. Провайдер берёт за
          каждые десять результатов: сто результатов — вдесятеро дороже.
          Доли цента — «меньше 0,01 $», а не «примерно 0,00 $» (проверка прода
          10.10.2026): «примерно» при «меньше» лишнее. */}
      <Text size="sm" c="dimmed">
        Выдача придёт {waitTitle(forecast.serp_wait_s)} и будет стоить{' '}
        {belowCent(forecast.serp_cost_usd) ? '' : 'примерно '}
        <b>{formatUsd(forecast.serp_cost_usd)}</b> — это другой счёт, не юниты Ahrefs. Потрачено
        нами юнитов {since(forecast)}: <b>{formatNumber(forecast.units_spent_in_cycle)}</b>.
      </Text>

      {forecast.affordable ? (
        <Alert color="green" title="Помещается">
          Смета считает худший случай — что все домены новые. За те, у которых данные ещё свежие,
          второй раз не платят, поэтому по факту обычно меньше.
        </Alert>
      ) : forecast.units_cap === null ? (
        <Alert color="red" title="Кап неизвестен">
          {forecast.cap_unknown ?? 'Размер ключа Ahrefs неизвестен'}: кап считается от размера
          ключа, и прогон не запустится, пока Ahrefs не ответит. Посчитайте смету ещё раз через
          несколько минут.
        </Alert>
      ) : (
        <ShortAlert forecast={forecast} />
      )}
    </Stack>
  );
}
