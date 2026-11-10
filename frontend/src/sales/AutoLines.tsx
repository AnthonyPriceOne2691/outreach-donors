/**
 * Автоотправка и автосборка — значок «включена» или «выключена» и строка сервера. Одна разметка у
 * «Очереди писем» (`GET /sales/queue`) и «Рассылки» (`GET /sales/mailing`): строки — тем же снимком,
 * каким судят проходы (`auto_lines.py`), и экран их не пересчитывает. Копия на второй вкладке
 * разошлась бы с первой на первой правке.
 *
 * Сеткой «значок | строка»: значки разной ширины, а строки начинаются с одной кромки. На узком окне
 * значок — над строкой: в колонке рядом с ним строка вставала в двадцать узких строк (снимок 390 px).
 *
 * **Коротко у значка, целиком — в «i»** (замечание владельца 10.11.2026: строки режима — простынями
 * по три строки на 1440 и по десять на 390). Короткую пишет сервер (`short`): что сейчас, сколько
 * писем в окне, хватает ли очереди и кого соберёт; причина остановки — в ней же. Темп прохода, кому
 * уходят письма и последняя автосборка — в «i» целой строкой. Совпали — «i» нет: пояснять нечего.
 */

import { Badge, Box, Group, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { Fragment } from 'react';

import type { SalesAutoLine } from '../api/salesTypes';
import { InfoHint } from '../components/InfoHint';

interface Props {
  autosend: SalesAutoLine;
  autobuild: SalesAutoLine;
}

export function AutoLines({ autosend, autobuild }: Props) {
  const narrow = useMediaQuery('(max-width: 36em)');
  const lines: [string, SalesAutoLine][] = [
    ['автоотправка', autosend],
    ['автосборка', autobuild],
  ];
  return (
    <Box
      px="md"
      style={{
        display: 'grid',
        gridTemplateColumns: narrow ? 'minmax(0, 1fr)' : 'max-content minmax(0, 1fr)',
        columnGap: 'var(--mantine-spacing-xs)',
        rowGap: 4,
        alignItems: 'start',
        justifyItems: 'start',
      }}
    >
      {lines.map(([title, auto]) => (
        <Fragment key={title}>
          <Badge
            variant="light"
            color={auto.enabled ? 'green' : 'gray'}
            className="queueAutoBadge"
            title={auto.enabled ? `${title} включена` : `${title} выключена`}
          >
            {title}
          </Badge>
          <Group gap={4} wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
            <Text size="sm" className="queueAuto">
              {auto.short}
            </Text>
            {auto.short === auto.line ? null : (
              <InfoHint name={`Подробно: ${title}`} width={420}>
                {auto.line}
              </InfoHint>
            )}
          </Group>
        </Fragment>
      ))}
    </Box>
  );
}
