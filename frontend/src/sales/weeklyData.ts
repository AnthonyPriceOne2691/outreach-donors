/**
 * Вкладка «Отчёт»: запрос недельного отчёта, неделя словами, как отчёт ушёл в группу продаж.
 *
 * **Неделя — по Москве и с понедельника**, как у сервера: экран берёт понедельники из ответа
 * (`weeks`) и дни не считает по часам браузера — иначе на стыке суток человек в другом поясе
 * выбрал бы не ту неделю.
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { readSalesWeekly } from '../api/salesWeekly';
import type { WeeklyDelivery } from '../api/salesWeekly';
import { formatDateTime } from '../format';

export const WEEKLY_QUERY_KEY = ['sales', 'weekly'] as const;

/** Отчёт недели; пусто — прошлая. Прежний ответ стоит, пока едет новый. */
export function useSalesWeekly(week: string) {
  return useQuery({
    queryKey: [...WEEKLY_QUERY_KEY, week],
    queryFn: () => readSalesWeekly(week),
    placeholderData: keepPreviousData,
  });
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** `YYYY-MM-DD` → «01.03.2027»; сдвиг — в сутках, без пояса браузера. */
function dayWords(iso: string, plus = 0): string {
  const [year, month, day] = iso.split('-').map(Number);
  const moment = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1) + plus * DAY_MS);
  const dd = String(moment.getUTCDate()).padStart(2, '0');
  const mm = String(moment.getUTCMonth() + 1).padStart(2, '0');
  return `${dd}.${mm}.${moment.getUTCFullYear()}`;
}

/** Неделя по её понедельнику: «01.03–07.03.2027». */
export function weekWords(monday: string): string {
  return `${dayWords(monday).slice(0, 5)}–${dayWords(monday, 6)}`;
}

export interface DeliveryWords {
  text: string;
  /** Цвет строки: не доставлен — янтарь, пока идут повторы, и роза, когда их больше нет. */
  color?: 'yellow' | 'red';
}

/** Как отчёт недели ушёл в группу продаж — словами. */
export function deliveryWords(delivery: WeeklyDelivery): DeliveryWords {
  const why = delivery.error ?? 'причина не записана';
  switch (delivery.status) {
    case 'sent':
      return { text: `Отчёт ушёл в группу продаж ${formatDateTime(delivery.sent_at)}.` };
    case 'pending':
      return { text: 'Отчёт отправляется в группу продаж.' };
    case 'undelivered':
      return delivery.due_at === null
        ? {
            text: `Не доставлен в группу продаж: ${why}. Повторов больше нет — отчёт здесь и в CSV.`,
            color: 'red',
          }
        : {
            text: `Не доставлен в группу продаж: ${why}. Повтор — не раньше ${formatDateTime(delivery.due_at)}.`,
            color: 'yellow',
          };
    case 'none':
      return {
        text:
          delivery.schedule === null
            ? 'В группу продаж не отправлялся.'
            : `В группу продаж не отправлялся. Отчёт уходит ${delivery.schedule}.`,
      };
    case 'off':
      // Так по умолчанию: наружу без человека продажи шлют только по решению. Имени настройки
      // на экране нет, как у «Модуль продаж выключен»: человеку — слова и кто это включает.
      return {
        text: 'Отчёт в группу продаж не уходит — отправку раз в неделю включает администратор.',
      };
  }
}
