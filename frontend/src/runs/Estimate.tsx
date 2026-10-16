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

import type { BudgetBinding } from '../api/forecastLimits';
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
 *  16.10.2026). Право «править пороги» по умолчанию есть и у оператора, и тот, у кого оно
 *  есть, поднимает сам; у кого нет — просит администратора (слово владельца). Ссылка —
 *  чернилами с подчёркиванием (`inkLink`), как ссылки посреди текста на главной. */
function RaiseCap() {
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
    <>поднимите кап на экране {usage}.</>
  ) : (
    <>попросите администратора поднять кап на экране {usage}.</>
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

const LIMIT_NAMES: Record<BudgetBinding, string> = {
  key: 'лимит ключа Ahrefs',
  workspace: 'лимит пространства Ahrefs',
  cap: 'кап',
  ceiling: 'ваш потолок',
};

const ONE_TITLES: Record<BudgetBinding, string> = {
  key: 'Не помещается: не хватает лимита ключа Ahrefs',
  workspace: 'Не помещается: не хватает лимита пространства Ahrefs',
  cap: 'Не помещается в кап',
  ceiling: 'Не помещается в ваш потолок',
};

/** Заголовок плашки «не помещается»: один лимит — он; несколько — проверить каждый
 *  (слово владельца 16.10.2026: «если и то, и другое — проверьте лимиты ключа и капа»). */
function shortTitle(short: BudgetBinding[]): string {
  const names = short.map((limit) => LIMIT_NAMES[limit]);
  const last = names.pop();
  if (last === undefined) return ONE_TITLES.cap;
  if (names.length === 0) return ONE_TITLES[short[0] ?? 'cap'];
  return `Не помещается: проверьте ${names.join(', ')} и ${last}`;
}

/** Строка плашки об одном лимите: сколько осталось и что с ним делать. «Кап здесь не
 *  поможет» — только когда кап хватает: не хватает и его, — поднимать надо оба. */
function ShortLine({ limit, forecast }: { limit: BudgetBinding; forecast: Forecast }) {
  const until = forecast.cycle_by_key ? ` до обнуления ${formatDay(forecast.next_reset)}` : '';
  const free = formatNumber(forecast.units_left);
  const alone = forecast.short_of.includes('cap') ? '' : ': кап здесь не поможет';
  switch (limit) {
    case 'key':
      return (
        <>
          На ключе Ahrefs свободно {free}
          {until} — увеличьте лимит ключа в самом Ahrefs{alone}.
        </>
      );
    case 'workspace':
      return (
        <>
          В пространстве Ahrefs свободно {free}
          {until} — это общий лимит всех его ключей; увеличьте его в самом Ahrefs{alone}.
        </>
      );
    case 'cap':
      return (
        <>
          По нашему капу осталось {formatNumber(forecast.cap_left)} из{' '}
          {formatNumber(forecast.units_cap ?? 0)}
          {until} — <RaiseCap />
        </>
      );
    default:
      return (
        <>
          Потолок этого прогона — {formatNumber(forecast.run_ceiling ?? 0)}: поднимите или уберите
          его в форме.
        </>
      );
  }
}

/** Не помещается — чего не хватает и что делать: каждый лимит своей строкой (16.10.2026:
 *  «поднимите кап» при кончившемся ключе Ahrefs вёл поднимать кап зря). */
function ShortAlert({ forecast }: { forecast: Forecast }) {
  const short = forecast.short_of.length > 0 ? forecast.short_of : [forecast.binding ?? 'cap'];
  return (
    <Alert color="red" title={shortTitle(short)}>
      <Stack gap={4}>
        <Text size="sm">Не хватает {formatNumber(forecast.shortfall)} юнитов.</Text>
        {short.map((limit) => (
          <Text size="sm" key={limit}>
            <ShortLine limit={limit} forecast={forecast} />
          </Text>
        ))}
        <Text size="sm">Или сократите список ключей или глубину выдачи.</Text>
      </Stack>
    </Alert>
  );
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
        <b>{formatUsd(forecast.serp_cost_usd)}</b> — это другой счёт, не юниты Ahrefs.
        {forecast.serp_balance_usd !== null && (
          <>
            {' '}
            На счету DataForSEO <b>{formatUsd(forecast.serp_balance_usd)}</b>.
          </>
        )}{' '}
        Потрачено нами юнитов {since(forecast)}:{' '}
        <b>{formatNumber(forecast.units_spent_in_cycle)}</b>.
      </Text>

      {/* Предохранитель счёта выдачи (16.10.2026): мало денег — прогон не начинается, и рядом
          с «пополните счёт» зелёного «помещается» нет. */}
      {forecast.serp_refusal !== null && (
        <Alert color="red" title="Пополните счёт DataForSEO">
          {forecast.serp_refusal}
        </Alert>
      )}
      {forecast.affordable ? (
        forecast.serp_refusal === null && (
          <Alert color="green" title="Помещается">
            Смета считает худший случай — что все домены новые. За те, у которых данные ещё свежие,
            второй раз не платят, поэтому по факту обычно меньше.
          </Alert>
        )
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
