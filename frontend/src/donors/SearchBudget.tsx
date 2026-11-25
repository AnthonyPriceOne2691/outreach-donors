/**
 * «найти» с кредитами Hunter (25.11.2026): нажатие сначала спрашивает смету, а не ставит поиск.
 *
 * «найти» ищет всем ждущим разом — тысячам сайтов, а платный поиск адреса тратит кредиты Hunter,
 * общие с проверкой адресов перед письмами и соседней системой. Поэтому нажатие спрашивает сервер
 * (`api/contacts/budget.py`): свободные кредиты сейчас — остаток учётки без запаса, проверок адресов
 * до конца месяца и долей идущих поисков. Ответ — у кнопки маленьким окном той же рамки, что у
 * `ConfirmPopover` (`ConfirmFrame`), только текст приходит с сервера:
 *
 * - хватает — «Найти адреса N сайтам?» и кнопка «Найти N»;
 * - не хватает — «Кредитов Hunter хватит примерно на M сайтов из N» и кнопка «Найти M»: число
 *   считается от остатка в эту минуту;
 * - свободных нет или остаток не узнать — слова и «Понятно»;
 * - платная ступень выключена — кредиты не нужны: поиск ставится сразу, как до сметы.
 *
 * Между сметой и нажатием кредиты мог занять автокруг: сервер проверяет ещё раз и отвечает
 * отказом словами (409) — его показывает уведомление поиска.
 */

import { Button } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { ContactsBudget } from '../api/contacts';
import { ConfirmFrame } from '../components/ConfirmPopover';
import { formatDateTime, formatNumber } from '../format';
import { notify } from '../notices';

/** Сколько сайтов берёт одно нажатие — всех ждущих, но не больше потолка сервера
 *  (`SEARCH_ALL_MAX`, `api/contacts/schemas.py`): больше он ответил бы отказом. */
export const SEARCH_ALL_MAX = 10_000;

interface Props {
  pending: number;
  ask: (sites: number) => Promise<ContactsBudget>;
  busy: boolean;
  onStart: (sites: number) => void;
}

/** Из чего сложилось свободное — одной строкой под вопросом. */
function partsLine(budget: ContactsBudget): string {
  const parts = [
    `на учётке ${formatNumber(budget.account_left ?? 0)}`,
    `запас ${formatNumber(budget.reserve ?? 0)}`,
    `проверка адресов до конца месяца ${formatNumber(budget.checks_hold ?? 0)}`,
  ];
  if ((budget.held ?? 0) > 0) parts.push(`идущие поиски ${formatNumber(budget.held ?? 0)}`);
  const stale =
    budget.live === false && budget.asked_at !== null
      ? ` Hunter не ответил — остаток на ${formatDateTime(budget.asked_at)}.`
      : '';
  return `Кредиты Hunter: ${parts.join(', ')}.${stale}`;
}

/** Вопрос и кнопка окна по смете: сколько сайтов поставит подтверждение; 0 — ставить нечего. */
function verdict(budget: ContactsBudget): { question: string; sites: number } {
  if (budget.unknown !== null)
    return { question: `Поиск не поставить: ${budget.unknown}.`, sites: 0 };
  if (budget.enough) {
    return {
      question: `Найти адреса ${formatNumber(budget.sites)} сайтам? Платных запросов примерно ${formatNumber(budget.needed)}, свободно ${formatNumber(budget.free)} кредитов.`,
      sites: budget.sites,
    };
  }
  if (budget.affordable > 0) {
    return {
      question: `Кредитов Hunter хватит примерно на ${formatNumber(budget.affordable)} сайтов из ${formatNumber(budget.sites)}: свободно ${formatNumber(budget.free)}.`,
      sites: budget.affordable,
    };
  }
  return { question: 'Свободных кредитов Hunter нет — поиск сейчас не поставить.', sites: 0 };
}

/** Окно по смете целиком: вопрос, строка частей, глагол кнопки и сколько сайтов он поставит. */
interface Offer {
  question: string;
  details: string | null;
  confirm: string | null;
  sites: number;
}

/** Сметы ещё нет — окно пустое и закрыто (его открывает ответ сервера). */
const NOTHING: Offer = { question: '', details: null, confirm: null, sites: 0 };

function offerOf(budget: ContactsBudget | undefined): Offer {
  if (budget === undefined) return NOTHING;
  const { question, sites } = verdict(budget);
  return {
    question,
    details: budget.unknown === null ? partsLine(budget) : null,
    confirm: sites > 0 ? `Найти ${formatNumber(sites)}` : null,
    sites,
  };
}

export function SearchBudget({ pending, ask, busy, onStart }: Props) {
  const [opened, setOpened] = useState(false);
  const sites = Math.min(pending, SEARCH_ALL_MAX);
  const estimate = useMutation({
    mutationFn: () => ask(sites),
    onSuccess: (budget) => {
      if (!budget.paid) {
        onStart(sites);
        return;
      }
      setOpened(true);
    },
    onError: (failure) =>
      notify({ title: 'Кредиты не узнать', message: refusalOf(failure), color: 'red' }),
  });
  const offer = offerOf(estimate.data);

  return (
    <ConfirmFrame
      opened={opened}
      onChange={setOpened}
      message={offer.question}
      details={offer.details}
      confirm={offer.confirm}
      // Кнопка подтверждения есть только у `sites > 0` (`offerOf`).
      onConfirm={() => onStart(offer.sites)}
      target={
        <Button
          variant="subtle"
          size="compact-sm"
          className="press"
          aria-label="найти адреса всем ждущим"
          loading={busy || estimate.isPending}
          onClick={() => estimate.mutate()}
        >
          найти
        </Button>
      }
    />
  );
}
