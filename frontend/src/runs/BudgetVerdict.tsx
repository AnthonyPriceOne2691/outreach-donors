/**
 * Бюджет сметы словами: во что упирается, помещается ли и что делать, если нет.
 *
 * Одна плашка на обе сметы экрана прогона — по ключам (`Estimate`) и по ссылкам
 * конкурентов (`CompetitorRun`): правила упора и слова лимитов одни (16.10.2026), и две
 * копии разошлись бы при первой правке слов. Различается только то, чем сократить прогон
 * и что сказать, когда помещается, — это приходит снаружи.
 */

import { Alert, Anchor, Stack, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import type { BudgetBinding, BudgetFigures } from '../api/forecastLimits';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { formatDay, formatNumber } from '../format';

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

/** Кап — на цикл ключа Ahrefs (16.10.2026): «по капу N из M» от размера ключа; размер
 *  неизвестен — так и сказано, а не «по капу 0 из 0». */
function capHint(figures: BudgetFigures): string {
  return figures.units_cap === null
    ? `кап неизвестен: ${figures.cap_unknown ?? 'размер ключа Ahrefs неизвестен'}`
    : `по капу ${formatNumber(figures.cap_left)} из ${formatNumber(figures.units_cap)}`;
}

/** Подпись бюджета: все числа, из которых он взят, а то, во что упирается, — первым и
 *  словом «упор» (16.10.2026): меньшее из трёх читалось без подсказки, какое оно. */
export function budgetHint(figures: BudgetFigures): string {
  const ahrefs = `у Ahrefs ${formatNumber(figures.units_left)}`;
  const cap = capHint(figures);
  const ceiling =
    figures.run_ceiling === null ? null : `ваш потолок ${formatNumber(figures.run_ceiling)}`;
  const bound = { key: ahrefs, workspace: ahrefs, cap, ceiling, none: null }[
    figures.binding ?? 'none'
  ];
  const rest = [ahrefs, cap, ceiling].filter((part) => part !== null && part !== bound);
  return (bound === null ? rest : [`упор — ${bound}`, ...rest]).join(' · ');
}

/** Плитка «Бюджет прогона» — меньшее из остатка у Ahrefs, капа и потолка, упор — первым. */
export function BudgetTile({ figures }: { figures: BudgetFigures }) {
  return (
    <Metric
      title="Бюджет прогона"
      value={formatNumber(figures.budget)}
      hint={budgetHint(figures)}
    />
  );
}

/** С какого дня считается наш расход: от обнуления ключа или — без его даты — с первого. */
export function since(figures: BudgetFigures): string {
  return figures.cycle_by_key
    ? `за цикл ключа, с ${formatDay(figures.cycle_start)}`
    : `с ${formatDay(figures.cycle_start)} — Ahrefs не назвал даты обнуления ключа`;
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
function ShortLine({ limit, figures }: { limit: BudgetBinding; figures: BudgetFigures }) {
  const until = figures.cycle_by_key ? ` до обнуления ${formatDay(figures.next_reset)}` : '';
  const free = formatNumber(figures.units_left);
  const alone = figures.short_of.includes('cap') ? '' : ': кап здесь не поможет';
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
          По нашему капу осталось {formatNumber(figures.cap_left)} из{' '}
          {formatNumber(figures.units_cap ?? 0)}
          {until} — <RaiseCap />
        </>
      );
    default:
      return (
        <>
          Потолок этого прогона — {formatNumber(figures.run_ceiling ?? 0)}: поднимите или уберите
          его в форме.
        </>
      );
  }
}

/** Не помещается — чего не хватает и что делать: каждый лимит своей строкой (16.10.2026:
 *  «поднимите кап» при кончившемся ключе Ahrefs вёл поднимать кап зря). */
function ShortAlert({ figures, trim }: { figures: BudgetFigures; trim: string }) {
  const short = figures.short_of.length > 0 ? figures.short_of : [figures.binding ?? 'cap'];
  return (
    <Alert color="red" title={shortTitle(short)}>
      <Stack gap={4}>
        <Text size="sm">Не хватает {formatNumber(figures.shortfall)} юнитов.</Text>
        {short.map((limit) => (
          <Text size="sm" key={limit}>
            <ShortLine limit={limit} figures={figures} />
          </Text>
        ))}
        <Text size="sm">{trim}</Text>
      </Stack>
    </Alert>
  );
}

interface Props {
  figures: BudgetFigures;
  /** Чем сократить прогон, когда не помещается: «Или сократите список ключей…». */
  trim: string;
  /** Что сказать, когда помещается: смета — худший случай, и какой именно. */
  fits: string;
  /** Запуск заблокирован другим (счёт выдачи пуст): зелёного «помещается» рядом нет. */
  blocked?: boolean;
}

/** Помещается, кап неизвестен или не хватает — одной плашкой. */
export function BudgetVerdict({ figures, trim, fits, blocked = false }: Props) {
  if (figures.affordable) {
    return blocked ? null : (
      <Alert color="green" title="Помещается">
        {fits}
      </Alert>
    );
  }
  if (figures.units_cap === null) {
    return (
      <Alert color="red" title="Кап неизвестен">
        {figures.cap_unknown ?? 'Размер ключа Ahrefs неизвестен'}: кап считается от размера ключа, и
        прогон не запустится, пока Ahrefs не ответит. Посчитайте смету ещё раз через несколько
        минут.
      </Alert>
    );
  }
  return <ShortAlert figures={figures} trim={trim} />;
}
