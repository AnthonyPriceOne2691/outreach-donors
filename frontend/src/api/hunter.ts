/**
 * Hunter на «Расходе» (слово Anthony 02.11.2026): общий остаток учётки и наш месячный потолок
 * проверок адреса. Всё — числами сервера: доли и «осталось» — тем же чтением, что у тревоги сторожа.
 *
 * Своим файлом, а не в `settings.ts`, как потолки модели: это своя тема, и `settings.ts` не растёт.
 */

import { request } from './client';

/** Счётчик учётки за её цикл: выбрано, всего и сколько осталось. Дробный у кредитов. */
export interface HunterCounter {
  used: number;
  available: number;
  remaining: number;
}

/** Учётка по снимку сторожа: общая с соседней системой и поиском адресов. */
export interface HunterAccount {
  plan: string;
  /** День сброса учётки: у неё свой цикл, не календарный месяц. */
  reset_on: string;
  /** Когда Hunter ответил в последний раз: снимок мог устареть. */
  asked_at: string;
  /** Общий счёт; `null` — у тарифа его нет, и поиск с проверкой считаются своими счётчиками. */
  credits: HunterCounter | null;
  searches: HunterCounter;
  /** Сколько проверок хватит на остаток: при общем счёте — тот же счёт в проверках. */
  verifications: HunterCounter;
  /** Выбрано от главного счётчика — целыми процентами вниз, как у тревоги. */
  used_percent: number;
}

/** Смена потолка: что поставили, кто и когда; `set_by` пусто — учётку удалили. */
export interface HunterCapChange {
  id: number;
  verify_cap: number;
  set_by: string | null;
  set_at: string;
}

/** Наш потолок проверок адреса на календарный месяц. */
export interface HunterMonth {
  /** Действующий потолок; `null` — числа нет, `problem` говорит почему. Ноль — проверки стоят. */
  cap: number | null;
  problem: string | null;
  /** `setting` — сохранён на экране; `env` — пока из настроек сервера. */
  source: 'setting' | 'env';
  spent: number;
  left: number | null;
  /** Выбрано от потолка; числа нет или ноль — `null`. */
  used_percent: number | null;
  /** С какого дня считается: первое число, а не сброс учётки. */
  since: string;
  /** Выше потолок не поставить: проверок тарифа, а снимка нет — граница от опечатки. */
  most: number;
  changes: HunterCapChange[];
}

/** Hunter на «Расходе». Учётки нет — `unknown` говорит почему; `month` пусто — потолок виден
 *  только с правом «Продажи». */
export interface HunterCard {
  account: HunterAccount | null;
  unknown: string | null;
  month: HunterMonth | null;
}

/** Новый потолок — с этой минуты; проход очистки возьмёт его при следующем старте. */
export function saveHunterCap(verifyCap: number): Promise<HunterCapChange> {
  return request<HunterCapChange>('/usage/hunter-cap', {
    method: 'POST',
    body: { verify_cap: verifyCap },
  });
}
