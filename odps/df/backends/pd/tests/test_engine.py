#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from datetime import timedelta, datetime
from random import randint

from odps.df.backends.tests.core import TestBase, to_str, tn, pandas_case
from odps.compat import unittest, irange as xrange
from odps.models import Schema
from odps.df.types import validate_data_type
from odps.df.expr.expressions import *
from odps.df.backends.pd.engine import PandasEngine
from odps.df.backends.odpssql.engine import ODPSEngine
from odps.df.backends.odpssql.types import df_schema_to_odps_schema
from odps.df import output_types, output_names, output, day, millisecond


@pandas_case
class Test(TestBase):
    def setup(self):
        datatypes = lambda *types: [validate_data_type(t) for t in types]
        schema = Schema.from_lists(['name', 'id', 'fid', 'isMale', 'scale', 'birth'],
                                   datatypes('string', 'int64', 'float64', 'boolean', 'decimal', 'datetime'))
        self.schema = df_schema_to_odps_schema(schema)

        import pandas as pd
        self.df = pd.DataFrame(None, columns=schema.names)
        self.expr = CollectionExpr(_source_data=self.df, _schema=schema)

        self.engine = PandasEngine(self.odps)
        self.odps_engine = ODPSEngine(self.odps)

        class FakeBar(object):
            def update(self, *args, **kwargs):
                pass
        self.faked_bar = FakeBar()

    def _gen_data(self, rows=None, data=None, nullable_field=None, value_range=None):
        if data is None:
            data = []
            for _ in range(rows):
                record = []
                for t in self.schema.types:
                    method = getattr(self, '_gen_random_%s' % t.name)
                    if t.name == 'bigint':
                        record.append(method(value_range=value_range))
                    else:
                        record.append(method())
                data.append(record)

            if nullable_field is not None:
                j = self.schema._name_indexes[nullable_field]
                for i, l in enumerate(data):
                    if i % 2 == 0:
                        data[i][j] = None

        import pandas as pd
        self.expr._source_data = pd.DataFrame(data, columns=self.schema.names)
        return data

    def testBase(self):
        data = self._gen_data(10, value_range=(-1000, 1000))

        expr = self.expr[::2]
        result = self._get_result(self.engine.execute(expr).values)
        self.assertEqual(data[::2], result)

        expr = self.expr[self.expr.id < 10]['name', lambda x: x.id]
        result = self._get_result(self.engine.execute(expr).values)
        self.assertEqual(len([it for it in data if it[1] < 10]), len(result))
        if len(result) > 0:
            self.assertEqual(2, len(result[0]))

        expr = self.expr[Scalar(3).rename('const'), self.expr.id, (self.expr.id + 1).rename('id2')]
        res = self.engine.execute(expr)
        result = self._get_result(res.values)
        self.assertEqual([c.name for c in res.columns], ['const', 'id', 'id2'])
        self.assertTrue(all(it[0] == 3 for it in result))
        self.assertEqual(len(data), len(result))
        self.assertEqual([it[1]+1 for it in data], [it[2] for it in result])

        expr = self.expr.sort('id')[1:5:2]
        res = self.engine.execute(expr)
        result = self._get_result(res.values)
        self.assertEqual(sorted(data, key=lambda it: it[1])[1:5:2], result)

        res = self.expr.head(10)
        result = self._get_result(res.values)
        self.assertEqual(data[:10], result)

        expr = self.expr.name.hash()
        res = self.engine.execute(expr)
        result = self._get_result(res.values)
        self.assertEqual([[hash(r[0])] for r in data], result),

        expr = self.expr.sample(parts=10)
        res = self.engine.execute(expr)
        self.assertGreaterEqual(len(res), 1)

    def testElement(self):
        data = self._gen_data(5, nullable_field='name')

        fields = [
            self.expr.name.isnull().rename('name1'),
            self.expr.name.notnull().rename('name2'),
            self.expr.name.fillna('test').rename('name3'),
            self.expr.id.isin([1, 2, 3]).rename('id1'),
            self.expr.id.isin(self.expr.fid.astype('int')).rename('id2'),
            self.expr.id.notin([1, 2, 3]).rename('id3'),
            self.expr.id.notin(self.expr.fid.astype('int')).rename('id4'),
            self.expr.id.between(self.expr.fid, 3).rename('id5'),
            self.expr.name.fillna('test').switch('test', 'test' + self.expr.name.fillna('test'),
                                                 'test2', 'test2' + self.expr.name.fillna('test'),
                                                 default=self.expr.name).rename('name4'),
            self.expr.id.cut([100, 200, 300],
                             labels=['xsmall', 'small', 'large', 'xlarge'],
                             include_under=True, include_over=True).rename('id6')
        ]

        expr = self.expr[fields]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(len(data), len(result))

        self.assertEqual(len([it for it in data if it[0] is None]),
                         len([it[0] for it in result if it[0]]))

        self.assertEqual(len([it[0] for it in data if it[0] is not None]),
                         len([it[1] for it in result if it[1]]))

        self.assertEqual([(it[0] if it[0] is not None else 'test') for it in data],
                         [it[2] for it in result])

        self.assertEqual([(it[1] in (1, 2, 3)) for it in data],
                         [it[3] for it in result])

        fids = [int(it[2]) for it in data]
        self.assertEqual([(it[1] in fids) for it in data],
                         [it[4] for it in result])

        self.assertEqual([(it[1] not in (1, 2, 3)) for it in data],
                         [it[5] for it in result])

        self.assertEqual([(it[1] not in fids) for it in data],
                         [it[6] for it in result])

        self.assertEqual([(it[2] <= it[1] <= 3) for it in data],
                         [it[7] for it in result])

        self.assertEqual([to_str('testtest' if it[0] is None else it[0]) for it in data],
                         [to_str(it[8]) for it in result])

        def get_val(val):
            if val <= 100:
                return 'xsmall'
            elif 100 < val <= 200:
                return 'small'
            elif 200 < val <= 300:
                return 'large'
            else:
                return 'xlarge'
        self.assertEqual([to_str(get_val(it[1])) for it in data], [to_str(it[9]) for it in result])

    def testArithmetic(self):
        data = self._gen_data(5, value_range=(-1000, 1000))

        fields = [
            (self.expr.id + 1).rename('id1'),
            (self.expr.fid - 1).rename('fid1'),
            (self.expr.scale * 2).rename('scale1'),
            (self.expr.scale + self.expr.id).rename('scale2'),
            (self.expr.id / 2).rename('id2'),
            (self.expr.id ** -2).rename('id3'),
            abs(self.expr.id).rename('id4'),
            (~self.expr.id).rename('id5'),
            (-self.expr.fid).rename('fid2'),
            (~self.expr.isMale).rename('isMale1'),
            (-self.expr.isMale).rename('isMale2'),
            (self.expr.id // 2).rename('id6'),
            (self.expr.birth + day(1).rename('birth1')),
            (self.expr.birth - (self.expr.birth - millisecond(10))).rename('birth2'),
        ]

        expr = self.expr[fields]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(len(data), len(result))

        self.assertEqual([it[1] + 1 for it in data],
                         [it[0] for it in result])

        self.assertAlmostEqual([it[2] - 1 for it in data],
                               [it[1] for it in result])

        self.assertEqual([it[4] * 2 for it in data],
                         [it[2] for it in result])

        self.assertEqual([it[4] + it[1] for it in data],
                         [it[3] for it in result])

        self.assertAlmostEqual([float(it[1]) / 2 for it in data],
                               [it[4] for it in result])

        self.assertEqual([int(it[1] ** -2) for it in data],
                         [it[5] for it in result])

        self.assertEqual([abs(it[1]) for it in data],
                         [it[6] for it in result])

        self.assertEqual([~it[1] for it in data],
                         [it[7] for it in result])

        self.assertAlmostEqual([-it[2] for it in data],
                               [it[8] for it in result])

        self.assertEqual([not it[3] for it in data],
                         [it[9] for it in result])

        self.assertEqual([it[1] // 2 for it in data],
                         [it[11] for it in result])

        self.assertEqual([it[5] + timedelta(days=1) for it in data],
                         [it[12] for it in result])

        self.assertEqual([10] * len(data), [it[13] for it in result])

    def testMath(self):
        data = self._gen_data(5, value_range=(1, 90))

        import numpy as np

        methods_to_fields = [
            (np.sin, self.expr.id.sin()),
            (np.cos, self.expr.id.cos()),
            (np.tan, self.expr.id.tan()),
            (np.sinh, self.expr.id.sinh()),
            (np.cosh, self.expr.id.cosh()),
            (np.tanh, self.expr.id.tanh()),
            (np.log, self.expr.id.log()),
            (np.log2, self.expr.id.log2()),
            (np.log10, self.expr.id.log10()),
            (np.log1p, self.expr.id.log1p()),
            (np.exp, self.expr.id.exp()),
            (np.expm1, self.expr.id.expm1()),
            (np.arccosh, self.expr.id.arccosh()),
            (np.arcsinh, self.expr.id.arcsinh()),
            (np.arctanh, self.expr.id.arctanh()),
            (np.arctan, self.expr.id.arctan()),
            (np.sqrt, self.expr.id.sqrt()),
            (np.abs, self.expr.id.abs()),
            (np.ceil, self.expr.id.ceil()),
            (np.floor, self.expr.id.floor()),
            (np.trunc, self.expr.id.trunc()),
        ]

        fields = [it[1].rename('id'+str(i)) for i, it in enumerate(methods_to_fields)]

        expr = self.expr[fields]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        for i, it in enumerate(methods_to_fields):
            method = it[0]

            first = [method(it[1]) for it in data]
            second = [it[i] for it in result]
            self.assertEqual(len(first), len(second))
            for it1, it2 in zip(first, second):
                if isinstance(it1, float) and np.isnan(it1) and it2 is None:
                    continue
                self.assertAlmostEqual(it1, it2)

    def testString(self):
        data = self._gen_data(5)

        methods_to_fields = [
            (lambda s: s.capitalize(), self.expr.name.capitalize()),
            (lambda s: data[0][0] in s, self.expr.name.contains(data[0][0], regex=False)),
            (lambda s: s.count(data[0][0]), self.expr.name.count(data[0][0])),
            (lambda s: s.endswith(data[0][0]), self.expr.name.endswith(data[0][0])),
            (lambda s: s.startswith(data[0][0]), self.expr.name.startswith(data[0][0])),
            (lambda s: s.find(data[0][0]), self.expr.name.find(data[0][0])),
            (lambda s: s.rfind(data[0][0]), self.expr.name.rfind(data[0][0])),
            (lambda s: s.replace(data[0][0], 'test'), self.expr.name.replace(data[0][0], 'test')),
            (lambda s: s[0], self.expr.name.get(0)),
            (lambda s: len(s), self.expr.name.len()),
            (lambda s: s.ljust(10), self.expr.name.ljust(10)),
            (lambda s: s.ljust(20, '*'), self.expr.name.ljust(20, fillchar='*')),
            (lambda s: s.rjust(10), self.expr.name.rjust(10)),
            (lambda s: s.rjust(20, '*'), self.expr.name.rjust(20, fillchar='*')),
            (lambda s: s * 4, self.expr.name.repeat(4)),
            (lambda s: s[2: 10: 2], self.expr.name.slice(2, 10, 2)),
            (lambda s: s[-5: -1], self.expr.name.slice(-5, -1)),
            (lambda s: s.title(), self.expr.name.title()),
            (lambda s: s.rjust(20, '0'), self.expr.name.zfill(20)),
            (lambda s: s.isalnum(), self.expr.name.isalnum()),
            (lambda s: s.isalpha(), self.expr.name.isalpha()),
            (lambda s: s.isdigit(), self.expr.name.isdigit()),
            (lambda s: s.isspace(), self.expr.name.isspace()),
            (lambda s: s.isupper(), self.expr.name.isupper()),
            (lambda s: s.istitle(), self.expr.name.istitle()),
            (lambda s: to_str(s).isnumeric(), self.expr.name.isnumeric()),
            (lambda s: to_str(s).isdecimal(), self.expr.name.isdecimal()),
        ]

        fields = [it[1].rename('id'+str(i)) for i, it in enumerate(methods_to_fields)]

        expr = self.expr[fields]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        for i, it in enumerate(methods_to_fields):
            method = it[0]

            first = [method(it[0]) for it in data]
            second = [it[i] for it in result]
            self.assertEqual(first, second)

    def testDatetime(self):
        data = self._gen_data(5)

        import pandas as pd

        methods_to_fields = [
            (lambda s: list(s.birth.dt.year.values), self.expr.birth.year),
            (lambda s: list(s.birth.dt.month.values), self.expr.birth.month),
            (lambda s: list(s.birth.dt.day.values), self.expr.birth.day),
            (lambda s: list(s.birth.dt.hour.values), self.expr.birth.hour),
            (lambda s: list(s.birth.dt.minute.values), self.expr.birth.minute),
            (lambda s: list(s.birth.dt.second.values), self.expr.birth.second),
            (lambda s: list(s.birth.dt.weekofyear.values), self.expr.birth.weekofyear),
            (lambda s: list(s.birth.dt.dayofweek.values), self.expr.birth.dayofweek),
            (lambda s: list(s.birth.dt.weekday.values), self.expr.birth.weekday),
            (lambda s: list(s.birth.dt.date.values), self.expr.birth.date),
            (lambda s: list(s.birth.dt.strftime('%Y%d')), self.expr.birth.strftime('%Y%d')),
            (lambda s: list(s.birth.dt.strftime('%Y%d').map(lambda x: datetime.strptime(x, '%Y%d'))),
             self.expr.birth.strftime('%Y%d').strptime('%Y%d')),
        ]

        fields = [it[1].rename('birth'+str(i)) for i, it in enumerate(methods_to_fields)]

        expr = self.expr[fields]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        df = pd.DataFrame(data, columns=self.schema.names)

        for i, it in enumerate(methods_to_fields):
            method = it[0]

            first = method(df)

            second = [it[i] for it in result]
            self.assertEqual(first, second)

    def testFuncion(self):
        data = [
            ['name1', 4, None, None, None, None],
            ['name2', 2, None, None, None, None],
            ['name1', 4, None, None, None, None],
            ['name1', 3, None, None, None, None],
        ]
        self._gen_data(data=data)

        expr = self.expr['id'].map(lambda x: x + 1)

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(result, [[r[1] + 1] for r in data])

        expr = self.expr['id'].mean().map(lambda x: x + 1)

        res = self.engine.execute(expr)
        ids = [r[1] for r in data]
        self.assertEqual(res, sum(ids) / float(len(ids)) + 1)

        expr = self.expr.apply(lambda row: row.name + str(row.id), axis=1, reduce=True).rename('name')

        res = self.engine.execute(expr)
        result = self._get_result(res)
        self.assertEqual(result, [[r[0] + str(r[1])] for r in data])

    def testFunctionResources(self):
        data = self._gen_data(5)

        class my_func(object):
            def __init__(self, resources):
                self.file_resource = resources[0]
                self.table_resource = resources[1]

                self.valid_ids = [int(l) for l in self.file_resource]
                self.valid_ids.extend([int(l[0]) for l in self.table_resource])

            def __call__(self, arg):
                if isinstance(arg, tuple):
                    if arg[1] in self.valid_ids:
                        return arg
                else:
                    if arg in self.valid_ids:
                        return arg

        def my_func2(resources):
            file_resource = resources[0]
            table_resource = resources[1]

            valid_ids = [int(l) for l in file_resource]
            valid_ids.extend([int(l[0]) for l in table_resource])

            def h(arg):
                if isinstance(arg, tuple):
                    if arg[1] in valid_ids:
                        return arg
                else:
                    if arg in valid_ids:
                        return arg

            return h

        try:
            self.odps.delete_resource('pyodps_tmp_file_resource')
        except:
            pass
        file_resource = self.odps.create_resource('pyodps_tmp_file_resource', 'file',
                                                  file_obj='\n'.join(str(r[1]) for r in data[:3]))
        self.odps.delete_table('pyodps_tmp_table', if_exists=True)
        t = self.odps.create_table('pyodps_tmp_table', Schema.from_lists(['id'], ['bigint']))
        with t.open_writer() as writer:
            writer.write([r[1: 2] for r in data[3: 4]])
        try:
            self.odps.delete_resource('pyodps_tmp_table_resource')
        except:
            pass
        table_resource = self.odps.create_resource('pyodps_tmp_table_resource', 'table',
                                                   table_name=t.name)

        try:
            expr = self.expr.id.map(my_func, resources=[file_resource, table_resource])

            res = self.engine.execute(expr)
            result = self._get_result(res)
            result = [r for r in result if r[0] is not None]

            self.assertEqual(sorted([[r[1]] for r in data[:4]]), sorted(result))

            expr = self.expr['name', 'id', 'fid']
            expr = expr.apply(my_func, axis=1, resources=[file_resource, table_resource],
                              names=expr.schema.names, types=expr.schema.types)

            res = self.engine.execute(expr)
            result = self._get_result(res)

            self.assertEqual(sorted([r[:3] for r in data[:4]]), sorted(result))

            expr = self.expr['name', 'id', 'fid']
            expr = expr.apply(my_func2, axis=1, resources=[file_resource, table_resource],
                              names=expr.schema.names, types=expr.schema.types)

            res = self.engine.execute(expr)
            result = self._get_result(res)

            self.assertEqual(sorted([r[:3] for r in data[:4]]), sorted(result))
        finally:
            try:
                file_resource.drop()
            except:
                pass
            try:
                t.drop()
            except:
                pass
            try:
                table_resource.drop()
            except:
                pass

    def testApply(self):
        data = [
            ['name1', 4, None, None, None, None],
            ['name2', 2, None, None, None, None],
            ['name1', 4, None, None, None, None],
            ['name1', 3, None, None, None, None],
        ]
        data = self._gen_data(data=data)

        def my_func(row):
            return row.name

        expr = self.expr['name', 'id'].apply(my_func, axis=1, names='name')

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual([r[0] for r in result], [r[0] for r in data])

        def my_func2(row):
            yield len(row.name)
            yield row.id

        expr = self.expr['name', 'id'].apply(my_func2, axis=1, names='cnt', types='int')

        res = self.engine.execute(expr)
        result = self._get_result(res)

        def gen_expected(data):
            for r in data:
                yield len(r[0])
                yield r[1]

        self.assertEqual(sorted([r[0] for r in result]), sorted([r for r in gen_expected(data)]))

    def testMapReduceByApplyDistributeSort(self):
        data = [
            ['name key', 4, 5.3, None, None, None],
            ['name', 2, 3.5, None, None, None],
            ['key', 4, 4.2, None, None, None],
            ['name', 3, 2.2, None, None, None],
            ['key name', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        def mapper(row):
            for word in row[0].split():
                yield word, 1

        class reducer(object):
            def __init__(self):
                self._curr = None
                self._cnt = 0

            def __call__(self, row):
                if self._curr is None:
                    self._curr = row.word
                elif self._curr != row.word:
                    yield (self._curr, self._cnt)
                    self._curr = row.word
                    self._cnt = 0
                self._cnt += row.count

            def close(self):
                if self._curr is not None:
                    yield (self._curr, self._cnt)

        expr = self.expr['name', ].apply(
            mapper, axis=1, names=['word', 'count'], types=['string', 'int'])
        expr = expr.groupby('word').sort('word').apply(
            reducer, names=['word', 'count'], types=['string', 'int'])

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [['key', 3], ['name', 4]]
        self.assertEqual(sorted(result), sorted(expected))

    def testMapReduce(self):
        data = [
            ['name key', 4, 5.3, None, None, None],
            ['name', 2, 3.5, None, None, None],
            ['key', 4, 4.2, None, None, None],
            ['name', 3, 2.2, None, None, None],
            ['key name', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        @output(['word', 'cnt'], ['string', 'int'])
        def mapper(row):
            for word in row[0].split():
                yield word, 1

        @output(['word', 'cnt'], ['string', 'int'])
        def reducer(keys):
            cnt = [0, ]

            def h(row, done):
                cnt[0] += row[1]
                if done:
                    yield keys[0], cnt[0]

            return h

        expr = self.expr['name', ].map_reduce(mapper, reducer, group='word')

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [['key', 3], ['name', 4]]
        self.assertEqual(sorted(result), sorted(expected))

        @output(['word', 'cnt'], ['string', 'int'])
        class reducer2(object):
            def __init__(self, keys):
                self.cnt = 0

            def __call__(self, row, done):
                self.cnt += row.cnt
                if done:
                    yield row.word, self.cnt

        expr = self.expr['name', ].map_reduce(mapper, reducer2, group='word')

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [['key', 3], ['name', 4]]
        self.assertEqual(sorted(result), sorted(expected))

    def testDistributeSort(self):
        data = [
            ['name', 4, 5.3, None, None, None],
            ['name', 2, 3.5, None, None, None],
            ['key', 4, 4.2, None, None, None],
            ['name', 3, 2.2, None, None, None],
            ['key', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        @output_names('name', 'id')
        @output_types('string', 'int')
        class reducer(object):
            def __init__(self):
                self._curr = None
                self._cnt = 0

            def __call__(self, row):
                if self._curr is None:
                    self._curr = row.name
                elif self._curr != row.name:
                    yield (self._curr, self._cnt)
                    self._curr = row.name
                    self._cnt = 0
                self._cnt += 1

            def close(self):
                if self._curr is not None:
                    yield (self._curr, self._cnt)

        expr = self.expr['name', ].groupby('name').sort('name').apply(reducer)

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [['key', 2], ['name', 3]]
        self.assertEqual(sorted(expected), sorted(result))

    def testSortDistinct(self):
        data = [
            ['name1', 4, None, None, None, None],
            ['name2', 2, None, None, None, None],
            ['name1', 4, None, None, None, None],
            ['name1', 3, None, None, None, None],
        ]
        self._gen_data(data=data)

        expr = self.expr.sort(['name', -self.expr.id]).distinct(['name', lambda x: x.id + 1])[:50]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(len(result), 3)

        expected = [
            ['name1', 5],
            ['name1', 4],
            ['name2', 3]
        ]
        self.assertEqual(expected, result)

    def testGroupbyAggregation(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        class Agg(object):
            def buffer(self):
                return [0]

            def __call__(self, buffer, val):
                buffer[0] += val

            def merge(self, buffer, pbuffer):
                buffer[0] += pbuffer[0]

            def getvalue(self, buffer):
                return buffer[0]

        expr = self.expr.groupby(['name', 'id'])[lambda x: x.fid.min() * 2 < 8] \
            .agg(self.expr.fid.max() + 1, new_id=self.expr.id.sum(),
                 new_id2=self.expr.id.agg(Agg))

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [
            ['name1', 3, 5.1, 6, 6],
            ['name2', 2, 4.5, 2, 2]
        ]

        result = sorted(result, key=lambda k: k[0])

        self.assertEqual(expected, result)

        expr = self.expr.groupby(Scalar(1).rename('s')).count()

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual([5], result[0])

        expr = self.expr.groupby(Scalar('const').rename('s')).id.sum()
        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual([16], result[0])

        field = self.expr.groupby('name').sort(['id', -self.expr.fid]).row_number()
        expr = self.expr['name', 'id', 'fid', field]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [
            ['name1', 3, 4.1, 1],
            ['name1', 3, 2.2, 2],
            ['name1', 4, 5.3, 3],
            ['name1', 4, 4.2, 4],
            ['name2', 2, 3.5, 1],
        ]

        result = sorted(result, key=lambda k: (k[0], k[1], -k[2]))

        self.assertEqual(expected, result)

        expr = self.expr.name.value_counts()[:25]

        expected = [
            ['name1', 4],
            ['name2', 1]
        ]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(expected, result)

        expr = self.expr.name.topk(25)

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(expected, result)

        expr = self.expr.groupby('name').count()

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual([it[1:] for it in expected], result)

        expected = [
            ['name1', 2],
            ['name2', 1]
        ]

        expr = self.expr.groupby('name').id.nunique()

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual([it[1:] for it in expected], result)

        expr = self.expr[self.expr['id'] > 2].name.value_counts()[:25]

        expected = [
            ['name1', 4]
        ]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(expected, result)

        expr = self.expr.groupby('name', Scalar(1).rename('constant'))\
            .agg(id=self.expr.id.sum())

        expected = [
            ['name1', 1, 14],
            ['name2', 1, 2]
        ]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(expected, result)

    def testJoinGroupby(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]

        schema2 = Schema.from_lists(['name', 'id2', 'id3'],
                                    [types.string, types.int64, types.int64])

        self._gen_data(data=data)

        data2 = [
            ['name1', 4, -1],
            ['name2', 1, -2]
        ]

        import pandas as pd
        expr2 = CollectionExpr(_source_data=pd.DataFrame(data2, columns=schema2.names),
                               _schema=schema2)

        expr = self.expr.join(expr2, on='name')[self.expr]
        expr = expr.groupby('id').agg(expr.fid.sum())

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = pd.DataFrame(data, columns=self.expr.schema.names).groupby('id').agg({'fid': 'sum'})
        self.assertEqual(expected.reset_index().values.tolist(), result)

    def testFilterGroupby(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        expr = self.expr.groupby(['name']).agg(id=self.expr.id.max())[lambda x: x.id > 3]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(len(result), 1)

        expected = [
            ['name1', 4]
        ]

        self.assertEqual(expected, result)

    def testGroupbyProjection(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        expr = self.expr.groupby('name').agg(id=self.expr.id.max())[
            lambda x: 't'+x.name, lambda x: x.id + 1]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [
            ['tname1', 5],
            ['tname2', 3]
        ]

        self.assertEqual(expected, result)

    def testWindowFunction(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 6.1, None, None, None],
        ]
        self._gen_data(data=data)

        expr = self.expr.groupby('name').id.cumsum()

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [[14]] * 4 + [[2]]
        self.assertEqual(sorted(expected), sorted(result))

        expr = self.expr.groupby('name').sort('fid').id.cummax()

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [[3], [4], [4], [4], [2]]
        self.assertEqual(sorted(expected), sorted(result))

        expr = self.expr[
            self.expr.groupby('name', 'id').sort('fid').id.cummean(),
            self.expr.groupby('name').id.cummedian()
        ]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [
            [3, 3.5], [3, 3.5], [4, 3.5], [4, 3.5], [2, 2]
        ]
        self.assertEqual(sorted(expected), sorted(result))

        expr = self.expr.groupby('name').mutate(id2=lambda x: x.id.cumcount(unique=True),
                                                fid=lambda x: x.fid.cummin(sort='id'))

        res = self.engine.execute(expr['name', 'id2', 'fid'])
        result = self._get_result(res)

        expected = [
            ['name1', 2, 2.2],
            ['name1', 2, 2.2],
            ['name1', 2, 2.2],
            ['name1', 2, 2.2],
            ['name2', 1, 3.5],
        ]
        self.assertEqual(sorted(expected), sorted(result))

        expr = self.expr[
            self.expr.id,
            self.expr.groupby('name').rank('id'),
            self.expr.groupby('name').dense_rank('fid', ascending=False),
            self.expr.groupby('name').row_number(sort=['id', 'fid'], ascending=[True, False]),
            self.expr.groupby('name').percent_rank('id'),
        ]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [
            [4, 3, 2, 3, float(2) / 3],
            [2, 1, 1, 1, 0.0],
            [4, 3, 3, 4, float(2) / 3],
            [3, 1, 4, 2, float(0) / 3],
            [3, 1, 1, 1, float(0) / 3]
        ]
        self.assertEqual(sorted(expected), sorted(result))

        expr = self.expr[
            self.expr.id,
            self.expr.groupby('name').id.lag(offset=3, default=0, sort=['id', 'fid']).rename('id2'),
            self.expr.groupby('name').id.lead(offset=1, default=-1,
                                              sort=['id', 'fid'], ascending=[False, False]).rename('id3'),
        ]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [
            [4, 3, 4],
            [2, 0, -1],
            [4, 0, 3],
            [3, 0, -1],
            [3, 0, 3]
        ]
        self.assertEqual(sorted(expected), sorted(result))

    def testWindowRewrite(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        expr = self.expr[self.expr.id - self.expr.id.mean() < 10][
            [lambda x: x.id - x.id.max()]][[lambda x: x.id - x.id.min()]][lambda x: x.id - x.id.std() > 0]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        import pandas as pd
        df = pd.DataFrame(data, columns=self.schema.names)
        expected = df.id - df.id.max()
        expected = expected - expected.min()
        expected = list(expected[expected - expected.std() > 0])

        self.assertEqual(expected, [it[0] for it in result])

    def testReduction(self):
        data = self._gen_data(rows=5, value_range=(-100, 100))

        import pandas as pd
        df = pd.DataFrame(data, columns=self.schema.names)

        class Agg(object):
            def buffer(self):
                return [0.0, 0]

            def __call__(self, buffer, val):
                buffer[0] += val
                buffer[1] += 1

            def merge(self, buffer, pbuffer):
                buffer[0] += pbuffer[0]
                buffer[1] += pbuffer[1]

            def getvalue(self, buffer):
                if buffer[1] == 0:
                    return 0.0
                return buffer[0] / buffer[1]

        methods_to_fields = [
            (lambda s: df.id.mean(), self.expr.id.mean()),
            (lambda s: len(df), self.expr.count()),
            (lambda s: df.id.var(ddof=0), self.expr.id.var(ddof=0)),
            (lambda s: df.id.std(ddof=0), self.expr.id.std(ddof=0)),
            (lambda s: df.id.median(), self.expr.id.median()),
            (lambda s: df.id.sum(), self.expr.id.sum()),
            (lambda s: df.id.min(), self.expr.id.min()),
            (lambda s: df.id.max(), self.expr.id.max()),
            (lambda s: df.isMale.min(), self.expr.isMale.min()),
            (lambda s: df.name.max(), self.expr.name.max()),
            (lambda s: df.birth.max(), self.expr.birth.max()),
            (lambda s: df.name.sum(), self.expr.name.sum()),
            (lambda s: df.isMale.sum(), self.expr.isMale.sum()),
            (lambda s: df.isMale.any(), self.expr.isMale.any()),
            (lambda s: df.isMale.all(), self.expr.isMale.all()),
            (lambda s: df.name.nunique(), self.expr.name.nunique()),
            (lambda s: df.id.mean(), self.expr.id.agg(Agg, rtype='float')),
            (lambda s: df.id.count(), self.expr.id.count()),
        ]

        fields = [it[1].rename('f'+str(i)) for i, it in enumerate(methods_to_fields)]

        expr = self.expr[fields]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        df = pd.DataFrame(data, columns=self.schema.names)

        for i, it in enumerate(methods_to_fields):
            method = it[0]

            first = method(df)
            second = [it[i] for it in result][0]
            if isinstance(first, float):
                self.assertAlmostEqual(first, second)
            else:
                self.assertEqual(first, second)

        self.assertEqual(self.engine.execute(self.expr.id.sum() + 1),
                         sum(it[1] for it in data) + 1)

        expr = self.expr['id', 'fid'].apply(Agg, types=['float'] * 2)

        expected = [[df.id.mean()], [df.fid.mean()]]

        res = self.engine.execute(expr)
        result = self._get_result(res)

        for first, second in zip(expected, result):
            first = first[0]
            second = second[0]

            if isinstance(first, float):
                self.assertAlmostEqual(first, second)
            else:
                self.assertEqual(first, second)

    def testUserDefinedAggregators(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        @output_types('float')
        class Aggregator(object):
            def buffer(self):
                return [0.0, 0]

            def __call__(self, buffer, val):
                buffer[0] += val
                buffer[1] += 1

            def merge(self, buffer, pbuffer):
                buffer[0] += pbuffer[0]
                buffer[1] += pbuffer[1]

            def getvalue(self, buffer):
                if buffer[1] == 0:
                    return 0.0
                return buffer[0] / buffer[1]

        expr = self.expr.id.agg(Aggregator)

        expected = float(16) / 5

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertAlmostEqual(expected, result)

        expr = self.expr.groupby(Scalar('const').rename('s')).id.agg(Aggregator)

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertAlmostEqual(expected, result[0][0])

        expr = self.expr.groupby('name').agg(self.expr.id.agg(Aggregator))

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [
            ['name1', float(14)/4],
            ['name2', 2]
        ]
        for expect_r, actual_r in zip(expected, result):
            self.assertEqual(expect_r[0], actual_r[0])
            self.assertAlmostEqual(expect_r[1], actual_r[1])

    def testJoin(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]

        schema2 = Schema.from_lists(['name', 'id2', 'id3'],
                                    [types.string, types.int64, types.int64])

        self._gen_data(data=data)

        data2 = [
            ['name1', 4, -1],
            ['name2', 1, -2]
        ]

        import pandas as pd
        expr2 = CollectionExpr(_source_data=pd.DataFrame(data2, columns=schema2.names),
                               _schema=schema2)

        expr = self.expr.join(expr2)['name', 'id2']

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertEqual(len(result), 5)
        expected = [
            [to_str('name1'), 4],
            [to_str('name2'), 1]
        ]
        self.assertTrue(all(it in expected for it in result))

        expr = self.expr.join(expr2, on=['name', ('id', 'id2')])[self.expr.name, expr2.id2]
        res = self.engine.execute(expr)
        result = self._get_result(res)
        self.assertEqual(len(result), 2)
        expected = [to_str('name1'), 4]
        self.assertTrue(all(it == expected for it in result))

        expr = self.expr.left_join(expr2, on=['name', ('id', 'id2')])[self.expr.name, expr2.id2]
        res = self.engine.execute(expr)
        result = self._get_result(res)
        expected = [
            ['name1', 4],
            ['name2', None],
            ['name1', 4],
            ['name1', None],
            ['name1', None]
        ]
        self.assertEqual(len(result), 5)
        self.assertTrue(all(it in expected for it in result))

        expr = self.expr.right_join(expr2, on=['name', ('id', 'id2')])[self.expr.name, expr2.id2]
        res = self.engine.execute(expr)
        result = self._get_result(res)
        expected = [
            ['name1', 4],
            ['name1', 4],
            [None, 1],
        ]
        self.assertEqual(len(result), 3)
        self.assertTrue(all(it in expected for it in result))

        expr = self.expr.outer_join(expr2, on=['name', ('id', 'id2')])[self.expr.name, expr2.id2]
        res = self.engine.execute(expr)
        result = self._get_result(res)
        expected = [
            ['name1', 4],
            ['name1', 4],
            ['name2', None],
            ['name1', None],
            ['name1', None],
            [None, 1],
        ]
        self.assertEqual(len(result), 6)
        self.assertTrue(all(it in expected for it in result))

    def testUnion(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]

        schema2 = Schema.from_lists(['name', 'id2', 'id3'],
                                    [types.string, types.int64, types.int64])

        self._gen_data(data=data)

        data2 = [
            ['name3', 5, -1],
            ['name4', 6, -2]
        ]

        import pandas as pd
        expr2 = CollectionExpr(_source_data=pd.DataFrame(data2, columns=schema2.names),
                               _schema=schema2)

        expr = self.expr['name', 'id'].distinct().union(expr2[expr2.id2.rename('id'), 'name'])

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expected = [
            ['name1', 4],
            ['name1', 3],
            ['name2', 2],
            ['name3', 5],
            ['name4', 6]
        ]

        result = sorted(result)
        expected = sorted(expected)

        self.assertEqual(len(result), len(expected))
        for e, r in zip(result, expected):
            self.assertEqual([to_str(t) for t in e],
                             [to_str(t) for t in r])

    def testHllc(self):
        names = [randint(0, 100000) for _ in xrange(100000)]
        data = [[n] + [None] * 5 for n in names]

        self._gen_data(data=data)

        expr = self.expr.name.hll_count()

        res = self.engine.execute(expr)
        result = self._get_result(res)

        expect = len(set(names))
        self.assertAlmostEqual(expect, result, delta=result*0.1)

    def testBloomFilter(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]

        data2 = [
            ['name1'],
            ['name3']
        ]

        self._gen_data(data=data)

        schema2 = Schema.from_lists(['name', ], [types.string])

        import pandas as pd
        expr2 = CollectionExpr(_source_data=pd.DataFrame(data2, columns=schema2.names),
                               _schema=schema2)

        expr = self.expr.bloom_filter('name', expr2[:1].name, capacity=10)

        res = self.engine.execute(expr)
        result = self._get_result(res)

        self.assertTrue(all(r[0] != 'name2' for r in result))

    def testPersist(self):
        data = [
            ['name1', 4, 5.3, None, None, None],
            ['name2', 2, 3.5, None, None, None],
            ['name1', 4, 4.2, None, None, None],
            ['name1', 3, 2.2, None, None, None],
            ['name1', 3, 4.1, None, None, None],
        ]
        self._gen_data(data=data)

        table_name = tn('pyodps_test_engine_persist_table')

        try:
            df = self.engine.persist(self.expr, table_name)

            res = df.to_pandas()
            result = self._get_result(res)
            self.assertEqual(len(result), 5)
            self.assertEqual(data, result)
        finally:
            self.odps.delete_table(table_name, if_exists=True)

        try:
            schema = Schema.from_lists(self.schema.names, self.schema.types, ['ds'], ['string'])
            self.odps.create_table(table_name, schema)
            df = self.engine.persist(self.expr, table_name, partition='ds=today', create_partition=True)

            res = self.odps_engine.execute(df)
            result = self._get_result(res)
            self.assertEqual(len(result), 5)
            self.assertEqual(data, [d[:-1] for d in result])
        finally:
            self.odps.delete_table(table_name, if_exists=True)

        try:
            self.engine.persist(self.expr, table_name, partitions=['name'])

            t = self.odps.get_table(table_name)
            self.assertEqual(2, len(list(t.partitions)))
            with t.open_reader(partition='name=name1', reopen=True) as r:
                self.assertEqual(4, r.count)
            with t.open_reader(partition='name=name2', reopen=True) as r:
                self.assertEqual(1, r.count)
        finally:
            self.odps.delete_table(table_name, if_exists=True)


if __name__ == '__main__':
    unittest.main()
