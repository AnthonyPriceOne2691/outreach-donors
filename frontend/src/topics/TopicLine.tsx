/**
 * Тематика строки — под доменом, а не своей колонкой.
 *
 * На 1440 с раскрытым меню колонкам «Доноров» остаётся 14 px сверх ширин, «Рассмотрения» —
 * 62 (замер 20.10.2026): колонка «Тематика» по самому длинному, что в ней бывает
 * («Промышленность» и вторая тема), увела бы обе таблицы в прокрутку вбок. Так уже решено
 * с ценой (12.10.2026): строкой под доменом, фильтр — над таблицей (`TopicFilter`).
 *
 * Основная тема — чернилами, вторая — тише; почему такая — в «i» сразу за подписью: он
 * открывается наведением, фокусом и касанием. Длинная пара тем переносится, и «i» уходит
 * со словом, а не встаёт у края ячейки. Нажатие на «i» не открывает карточку донора: строка
 * таблицы доноров — ссылка целиком.
 */

import { Text, VisuallyHidden } from '@mantine/core';

import type { TopicFields } from '../api/topics';
import { InfoHint } from '../components/InfoHint';

/** Подпись темы целиком: «Еда и напитки» не рвётся посередине, перенос — только у « · ». */
function whole(label: string): string {
  return label.replace(/ /g, '\u00a0');
}

export function TopicLine({ row }: { row: TopicFields }) {
  const { topic, topic_secondary: second, topic_reason: reason } = row;
  if (!topic) return null;
  return (
    <Text size="xs" c="var(--ink)" className="topicLine" data-topic={topic.code}>
      <VisuallyHidden>Тематика: </VisuallyHidden>
      {whole(topic.label)}
      {second && (
        <Text span size="xs" c="dimmed">
          {' · '}
          {whole(second.label)}
        </Text>
      )}
      {reason && (
        // Щелчок по «i» — подсказка, а не переход в карточку строки.
        <span
          style={{ display: 'inline-block', verticalAlign: 'middle', marginLeft: 2 }}
          onClick={(event) => event.stopPropagation()}
        >
          <InfoHint name={`Почему тематика «${topic.label}»`} width={260}>
            {reason}
          </InfoHint>
        </span>
      )}
    </Text>
  );
}
