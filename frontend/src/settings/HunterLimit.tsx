/**
 * Hunter на экране «Расход» (слово Anthony 02.11.2026: «на фронт выводился общий остаток, именно
 * по учётке»): крупно — сколько осталось на всей учётке, ниже — наш месячный потолок проверок.
 *
 * **Остаток — всей учётки, а не наших трат**: она общая с соседней системой и поиском адресов, и
 * наш журнал расхода её трат не видит. Число — из снимка сторожа, опрос раз в десять минут: строка
 * «проверено» говорит, насколько оно свежее, и Hunter недоступен — остаётся прежнее со своим
 * временем.
 *
 * **Один счёт — одно число**: у учётки с общим счётом поиск стоит кредит, проверка — половину, и
 * поиски с проверками — тот же счёт в других единицах. Поэтому крупно — кредиты, а рядом «хватит на
 * N проверок»; три независимых счётчика читались бы тремя запасами. Общего счёта у тарифа нет —
 * крупно проверки, рядом поиски.
 *
 * **Две границы названы словами**: потолок считается с первого числа, учётка обнуляется в свой день.
 *
 * **Потолок — только с правом «Продажи»**: проверки адреса — их расход. Меняет учётка с правом
 * «менять потолки расхода» (`HunterCapModal.tsx`). Нет числа — слова сервера, почему.
 */

import { useState } from 'react';

import { permissionTitle } from '../api/labels';
import type { HunterAccount, HunterCard, HunterMonth } from '../api/hunter';
import { useSession } from '../auth/AuthProvider';
import { Meter } from '../components/Meter';
import { capitalized, formatDay, formatNumber } from '../format';
import { changedBy } from './CapDialog';
import { HunterCapModal, STOPPED } from './HunterCapModal';
import { checked, LimitCard, LimitLine } from './LimitCard';

/** Кредиты бывают дробными: проверка стоит половину, и «9 292» вместо «9 291,5» было бы неправдой. */
const CREDITS = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });

/** Главное число учётки: общий счёт, а без него — проверки. */
function mainOf(account: HunterAccount): { value: string; of: string; spent: number; cap: number } {
  const { credits, verifications } = account;
  if (credits !== null) {
    return {
      value: CREDITS.format(credits.remaining),
      of: `из ${formatNumber(credits.available)} кредитов`,
      spent: credits.available - credits.remaining,
      cap: credits.available,
    };
  }
  return {
    value: formatNumber(verifications.remaining),
    of: `из ${formatNumber(verifications.available)} проверок адреса`,
    spent: verifications.available - verifications.remaining,
    cap: verifications.available,
  };
}

/** Что значит число: на сколько хватит — одним счётом, а без него поиски своей строкой. */
function noteOf(account: HunterAccount): string {
  const chosen = `Выбрано ${account.used_percent}%`;
  const checks = formatNumber(account.verifications.remaining);
  if (account.credits !== null) return `${chosen} · хватит на ${checks} проверок адреса`;
  return `${chosen} · поисков адреса осталось ${formatNumber(account.searches.remaining)}`;
}

/** Выбрано за месяц: от потолка числом; потолок ноль или числа нет — просто сколько выбрано. */
function monthLine(month: HunterMonth): string {
  const from = `Выбрано с ${formatDay(month.since)}: ${formatNumber(month.spent)}`;
  if (month.cap === null || month.cap === 0) return from;
  return `${from} из ${formatNumber(month.cap)} — ${month.used_percent ?? 0}%, осталось ${formatNumber(month.left)}`;
}

/** Подвал: что задано. Числа нет — слова сервера, почему; ещё не сохраняли — откуда потолок. */
function capLine(month: HunterMonth): string {
  if (month.cap === null) return capitalized(month.problem ?? 'потолка проверок нет');
  const words = month.cap === 0 ? STOPPED : `${formatNumber(month.cap)} в месяц`;
  return `Потолок: ${words}${month.source === 'env' ? ' — пока из настроек сервера' : ''}`;
}

/** Кто и когда поставил потолок; смен нет — на экране его не меняли. */
function changedLine(month: HunterMonth): string {
  const [last] = month.changes;
  return last === undefined
    ? 'Потолок на экране ещё не меняли'
    : `Потолок изменён: ${changedBy(last)}`;
}

function MonthCap({ month, resetOn }: { month: HunterMonth; resetOn: string | null }) {
  return (
    <>
      <LimitLine strong>Проверки адреса за месяц</LimitLine>
      <LimitLine>{monthLine(month)}</LimitLine>
      {month.cap !== null && month.cap > 0 && (
        <Meter spent={month.spent} cap={month.cap} label="Выбрано от потолка проверок за месяц" />
      )}
      <LimitLine>
        {resetOn === null
          ? 'Потолок считается с первого числа'
          : `Потолок считается с первого числа, а учётка обнуляется ${formatDay(resetOn)}`}
      </LimitLine>
      <LimitLine>{changedLine(month)}</LimitLine>
    </>
  );
}

/** Верх карточки: остаток учётки, а снимка нет — почему, словами сервера. */
function accountProps({ account, unknown }: HunterCard) {
  if (account === null) return { note: unknown ?? 'Hunter ещё не отвечал' };
  const main = mainOf(account);
  return {
    value: main.value,
    of: main.of,
    gauge: { spent: main.spent, cap: main.cap, label: 'Выбрано от счёта учётки Hunter' },
    note: noteOf(account),
    when: `Сброс ${formatDay(account.reset_on)} · ${checked(account.asked_at)}`,
  };
}

/** Под чертой: что за учётка и наш потолок; потолок виден только с правом «Продажи». */
function Details({ account, month }: HunterCard) {
  return (
    <>
      {account !== null && (
        <LimitLine>
          Тариф {account.plan} · учётка общая с соседней системой и поиском адресов
        </LimitLine>
      )}
      {month === null ? (
        <LimitLine>Потолок проверок адреса виден с правом «{permissionTitle('sales')}»</LimitLine>
      ) : (
        <MonthCap month={month} resetOn={account?.reset_on ?? null} />
      )}
    </>
  );
}

export function HunterLimit({ hunter }: { hunter: HunterCard }) {
  const { can } = useSession();
  const [editing, setEditing] = useState(false);
  const { month } = hunter;
  return (
    <>
      <LimitCard
        title="Hunter"
        caption="Осталось на учётке"
        {...accountProps(hunter)}
        footer={month === null ? undefined : capLine(month)}
        action={{
          label: 'Изменить потолок',
          allowed: can('spend'),
          who: `Потолок меняет учётка с правом «${permissionTitle('spend')}»`,
          onClick: () => setEditing(true),
        }}
      >
        <Details {...hunter} />
      </LimitCard>
      {/* Окно монтируется на каждое открытие — и открывается на действующем потолке. */}
      {editing && month !== null && (
        <HunterCapModal month={month} onClose={() => setEditing(false)} />
      )}
    </>
  );
}
