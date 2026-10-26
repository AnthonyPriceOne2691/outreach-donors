/**
 * Строка «Очереди писем»: к какому дню уйдёт очередь продаж — и вход в «Рассылку», где план по
 * дням, факт и ящики.
 *
 * День — из прогноза рассылки (`queue_eta.day`), тем же ответом и ключом кэша, что у вкладки
 * «Рассылка» (`useSalesForecast`): срок на двух вкладках — одно число. Прогноза нет (не пришёл,
 * отказ) или срока нет (очередь пуста, письма не уходят) — нет и строки: почему, говорит вкладка
 * «Рассылка», а на очереди причины не уйти — свои, словами сервера (подключение, цепочки).
 */

import { Anchor, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import { salesTabLink } from './leadFilters';
import { dayLabel, useSalesForecast } from './mailingData';

export function QueueEtaLine({ hypothesis }: { hypothesis: number | null }) {
  const forecast = useSalesForecast();
  const day = forecast.data?.queue_eta.day ?? null;
  if (day === null) return null;
  return (
    <Text size="sm" px="md" className="queueEta">
      Очередь уйдёт к {dayLabel(day)} —{' '}
      {/* Чернилами с подчёркиванием, как ссылки посреди текста: бирюза на светлом стекле
          не держала норму (`inkLink`, 07.10.2026). */}
      <Anchor
        component={Link}
        to={salesTabLink('mailing', hypothesis)}
        size="sm"
        fw={500}
        c="var(--ink)"
        underline="always"
        className="inkLink"
      >
        подробнее в «Рассылке»
      </Anchor>
    </Text>
  );
}
