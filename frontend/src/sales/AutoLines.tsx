/**
 * Автоотправка и автосборка — значок «включена» или «выключена» и строка сервера. Одна разметка у
 * «Очереди писем» (`GET /sales/queue`) и «Рассылки» (`GET /sales/mailing`): строки — тем же снимком,
 * каким судят проходы (`auto_lines.py`), и экран их не пересчитывает. Копия на второй вкладке
 * разошлась бы с первой на первой правке.
 *
 * Сеткой «значок | строка»: значки разной ширины, а строки начинаются с одной кромки. На узком окне
 * значок — над строкой: в колонке рядом с ним строка вставала в двадцать узких строк (снимок 390 px).
 */

import { Badge, Box, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { Fragment } from 'react';

import type { SalesAutoLine } from '../api/salesTypes';

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
          <Text size="sm" className="queueAuto">
            {auto.line}
          </Text>
        </Fragment>
      ))}
    </Box>
  );
}
