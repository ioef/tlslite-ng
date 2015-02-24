# Copyright (c) 2014, Hubert Kario
#
# See the LICENSE file for legal information regarding use of this file.

import unittest
try:
    import mock
    from mock import call
except ImportError:
    import unittest.mock as mock
    from unittest.mock import call

import socket
import tlslite.tlsrecordlayer
from tlslite.tlsrecordlayer import TLSRecordLayer
from tlslite.messages import ClientHello, ServerHello, Certificate, \
        ServerHelloDone, ClientKeyExchange, ChangeCipherSpec, Finished, \
        RecordHeader3, ApplicationData
from tlslite.extensions import TLSExtension
from tlslite.constants import ContentType, HandshakeType, CipherSuite, \
        CertificateType
from tlslite.errors import TLSLocalAlert, TLSAbruptCloseError, \
        TLSClosedConnectionError
from tlslite.mathtls import calcMasterSecret, PRF_1_2
from tlslite.x509 import X509
from tlslite.x509certchain import X509CertChain
from tlslite.utils.keyfactory import parsePEMKey
from tlslite.utils.codec import Parser
from unit_tests.mocksock import MockSocket

from tlslite.tlsconnection import TLSConnection

class TestTLSRecordLayer(unittest.TestCase):
    def test___init__(self):
        record_layer = TLSRecordLayer(None)

        self.assertIsInstance(record_layer, TLSRecordLayer)

    def test_getCipherName(self):
        record_layer = TLSRecordLayer(None)

        self.assertEqual(None, record_layer.getCipherName())

    def test_getCipherName_with_initialised_context(self):
        record_layer = TLSRecordLayer(None)
        record_layer.version = (3, 0)

        record_layer.calcPendingStates(
                CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA,
                bytearray(48), bytearray(32), bytearray(32), None)

        record_layer.changeWriteState()

        self.assertEqual('aes128', record_layer.getCipherName())

    def test_getCipherImplementation(self):
        record_layer = TLSRecordLayer(None)

        self.assertEqual(None, record_layer.getCipherImplementation())

    def test_getCipherImplementation_with_initialised_context(self):
        record_layer = TLSRecordLayer(None)
        record_layer.version = (3, 0)

        record_layer.calcPendingStates(
                CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA,
                bytearray(48), bytearray(32), bytearray(32), None)

        record_layer.changeWriteState()

        if tlslite.tlsrecordlayer.m2cryptoLoaded:
            self.assertEqual('openssl', record_layer.getCipherImplementation())
        else:
            self.assertEqual('python', record_layer.getCipherImplementation())

    def test_getVersionName_with_SSL3(self):
        record_layer = TLSRecordLayer(None)
        record_layer.version = (3, 0)

        self.assertEqual('SSL 3.0', record_layer.getVersionName())

    def test_getVersionName_with_TLS10(self):
        record_layer = TLSRecordLayer(None)
        record_layer.version = (3, 1)

        self.assertEqual('TLS 1.0', record_layer.getVersionName())

    def test_getVersionName_with_TLS11(self):
        record_layer = TLSRecordLayer(None)
        record_layer.version = (3, 2)

        self.assertEqual('TLS 1.1', record_layer.getVersionName())

    def test_getVersionName_with_TLS12(self):
        record_layer = TLSRecordLayer(None)
        record_layer.version = (3, 3)

        self.assertEqual('TLS 1.2', record_layer.getVersionName())

    def test_sendMessage(self):
        mock_sock = mock.create_autospec(socket.socket)

        record_layer = TLSRecordLayer(mock_sock)

        client_hello = ClientHello().create((3,3), bytearray(32), bytearray(0),
                [])

        gen = record_layer.sendMessage(client_hello)
        next(gen)

        self.assertEqual(1, mock_sock.send.call_count)

    def test_sendMessage_with_large_message(self):

        mock_sock = MockSocket(bytearray(0))

        record_layer = TLSRecordLayer(mock_sock)

        client_hello = ClientHello().create((3,3), bytearray(32), bytearray(0),
                [x for x in range(2**15-1)])

        gen = record_layer.sendMessage(client_hello)

        for result in gen:
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        # sent more than one message
        self.assertTrue(len(mock_sock.sent) > 1)

        # The maximum length that can be sent in single record is 2**14
        # record layer adds 5 byte on top of that
        for msg in mock_sock.sent:
            self.assertTrue(len(msg) <= 2**14 + 5)

    def test__getMsg(self):

        mock_sock = MockSocket(
                bytearray(
                b'\x16' +           # handshake
                b'\x03\x03' +       # TLSv1.2
                b'\x00\x3a' +       # payload length
                b'\x02' +           # Server Hello
                b'\x00\x00\x36' +   # hello length
                b'\x03\x03' +       # TLSv1.2
                b'\x00'*32 +        # random
                b'\x00' +           # session ID length
                b'\x00\x2f' +       # cipher suite selected (AES128-SHA)
                b'\x00' +           # compression null
                b'\x00\x0e' +       # extensions length
                b'\xff\x01' +       # renegotiation_info
                b'\x00\x01' +       # ext length
                b'\x00' +           # renegotiation info ext length - 0
                b'\x00\x23' +       # session_ticket
                b'\x00\x00' +       # ext length
                b'\x00\x0f' +       # heartbeat extension
                b'\x00\x01' +       # ext length
                b'\x01'))           # peer is allowed to send requests

        record_layer = TLSRecordLayer(mock_sock)

        gen = record_layer._getMsg(ContentType.handshake,
                HandshakeType.server_hello)

        message = next(gen)

        self.assertEqual(ServerHello, type(message))
        self.assertEqual((3,3), message.server_version)
        self.assertEqual(0x002f, message.cipher_suite)

    def test__getMsg_with_fragmented_message(self):

        mock_sock = MockSocket(
                bytearray(
                b'\x16' +           # handshake
                b'\x03\x03' +       # TLSv1.2
                b'\x00\x06' +       # payload length
                b'\x02' +           # Server Hello
                b'\x00\x00\x36' +   # hello length
                b'\x03\x03' +       # TLSv1.2
                # fragment end
                b'\x16' +           # type - handshake
                b'\x03\x03' +       # TLSv1.2
                b'\x00\x34' +       # payload length:
                b'\x00'*32 +        # random
                b'\x00' +           # session ID length
                b'\x00\x2f' +       # cipher suite selected (AES128-SHA)
                b'\x00' +           # compression null
                b'\x00\x0e' +       # extensions length
                b'\xff\x01' +       # renegotiation_info
                b'\x00\x01' +       # ext length
                b'\x00' +           # renegotiation info ext length - 0
                b'\x00\x23' +       # session_ticket
                b'\x00\x00' +       # ext length
                b'\x00\x0f' +       # heartbeat extension
                b'\x00\x01' +       # ext length
                b'\x01'))           # peer is allowed to send requests

        record_layer = TLSRecordLayer(mock_sock)

        gen = record_layer._getMsg(ContentType.handshake,
                HandshakeType.server_hello)

        message = next(gen)

        if message in (0,1):
            raise Exception("blocking")

        self.assertEqual(ServerHello, type(message))
        self.assertEqual((3,3), message.server_version)
        self.assertEqual(0x002f, message.cipher_suite)

    def test__getMsg_with_oversized_message(self):

        mock_sock = MockSocket(
                bytearray(
                b'\x16' +           # handshake
                b'\x03\x03' +       # TLSv1.2
                b'\x40\x01' +       # payload length 2**14+1
                b'\x02' +           # Server Hello
                b'\x00\x3f\xfd' +   # hello length 2**14+1-1-3
                b'\x03\x03' +       # TLSv1.2
                b'\x00'*32 +        # random
                b'\x00' +           # session ID length
                b'\x00\x2f' +       # cipher suite selected (AES128-SHA)
                b'\x00' +           # compression null
                b'\x3f\xd5' +       # extensions length: 2**14+1-1-3-2-32-6
                b'\xff\xff' +       # extension type (padding)
                b'\x3f\xd1' +       # extension length: 2**14+1-1-3-2-32-6-4
                b'\x00'*16337       # value
                ))

        record_layer = TLSRecordLayer(mock_sock)

        gen = record_layer._getMsg(ContentType.handshake,
                HandshakeType.server_hello)

        with self.assertRaises(TLSLocalAlert):
            message = next(gen)


    def test_full_connection_with_RSA_kex(self):

        clnt_sock, srv_sock = socket.socketpair()

        #
        # client part
        #
        record_layer = TLSRecordLayer(clnt_sock)

        record_layer.client = True
        record_layer.version = (3,3)

        client_hello = ClientHello()
        client_hello = client_hello.create((3,3), bytearray(32),
                bytearray(0), [CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA],
                None, None, False, False, None)

        record_layer._handshakeHashes.update(client_hello.write())
        for result in record_layer.sendMessage(client_hello):
            if result in (0,1):
                raise Exception("blocking socket")

        #
        # server part
        #

        srv_record_layer = TLSRecordLayer(srv_sock)

        srv_raw_certificate = str(
            "-----BEGIN CERTIFICATE-----\n"\
            "MIIB9jCCAV+gAwIBAgIJAMyn9DpsTG55MA0GCSqGSIb3DQEBCwUAMBQxEjAQBgNV\n"\
            "BAMMCWxvY2FsaG9zdDAeFw0xNTAxMjExNDQzMDFaFw0xNTAyMjAxNDQzMDFaMBQx\n"\
            "EjAQBgNVBAMMCWxvY2FsaG9zdDCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEA\n"\
            "0QkEeakSyV/LMtTeARdRtX5pdbzVuUuqOIdz3lg7YOyRJ/oyLTPzWXpKxr//t4FP\n"\
            "QvYsSJiVOlPk895FNu6sNF/uJQyQGfFWYKkE6fzFifQ6s9kssskFlL1DVI/dD/Zn\n"\
            "7sgzua2P1SyLJHQTTs1MtMb170/fX2EBPkDz+2kYKN0CAwEAAaNQME4wHQYDVR0O\n"\
            "BBYEFJtvXbRmxRFXYVMOPH/29pXCpGmLMB8GA1UdIwQYMBaAFJtvXbRmxRFXYVMO\n"\
            "PH/29pXCpGmLMAwGA1UdEwQFMAMBAf8wDQYJKoZIhvcNAQELBQADgYEAkOgC7LP/\n"\
            "Rd6uJXY28HlD2K+/hMh1C3SRT855ggiCMiwstTHACGgNM+AZNqt6k8nSfXc6k1gw\n"\
            "5a7SGjzkWzMaZC3ChBeCzt/vIAGlMyXeqTRhjTCdc/ygRv3NPrhUKKsxUYyXRk5v\n"\
            "g/g6MwxzXfQP3IyFu3a9Jia/P89Z1rQCNRY=\n"\
            "-----END CERTIFICATE-----\n"\
            )

        srv_raw_key = str(
            "-----BEGIN RSA PRIVATE KEY-----\n"\
            "MIICXQIBAAKBgQDRCQR5qRLJX8sy1N4BF1G1fml1vNW5S6o4h3PeWDtg7JEn+jIt\n"\
            "M/NZekrGv/+3gU9C9ixImJU6U+Tz3kU27qw0X+4lDJAZ8VZgqQTp/MWJ9Dqz2Syy\n"\
            "yQWUvUNUj90P9mfuyDO5rY/VLIskdBNOzUy0xvXvT99fYQE+QPP7aRgo3QIDAQAB\n"\
            "AoGAVSLbE8HsyN+fHwDbuo4I1Wa7BRz33xQWLBfe9TvyUzOGm0WnkgmKn3LTacdh\n"\
            "GxgrdBZXSun6PVtV8I0im5DxyVaNdi33sp+PIkZU386f1VUqcnYnmgsnsUQEBJQu\n"\
            "fUZmgNM+bfR+Rfli4Mew8lQ0sorZ+d2/5fsM0g80Qhi5M3ECQQDvXeCyrcy0u/HZ\n"\
            "FNjIloyXaAIvavZ6Lc6gfznCSfHc5YwplOY7dIWp8FRRJcyXkA370l5dJ0EXj5Gx\n"\
            "udV9QQ43AkEA34+RxjRk4DT7Zo+tbM/Fkoi7jh1/0hFkU5NDHweJeH/mJseiHtsH\n"\
            "KOcPGtEGBBqT2KNPWVz4Fj19LiUmmjWXiwJBAIBs49O5/+ywMdAAqVblv0S0nweF\n"\
            "4fwne4cM+5ZMSiH0XsEojGY13EkTEon/N8fRmE8VzV85YmkbtFWgmPR85P0CQQCs\n"\
            "elWbN10EZZv3+q1wH7RsYzVgZX3yEhz3JcxJKkVzRCnKjYaUi6MweWN76vvbOq4K\n"\
            "G6Tiawm0Duh/K4ZmvyYVAkBppE5RRQqXiv1KF9bArcAJHvLm0vnHPpf1yIQr5bW6\n"\
            "njBuL4qcxlaKJVGRXT7yFtj2fj0gv3914jY2suWqp8XJ\n"\
            "-----END RSA PRIVATE KEY-----\n"\
            )

        srv_private_key = parsePEMKey(srv_raw_key, private=True)
        srv_cert_chain = X509CertChain([X509().parse(srv_raw_certificate)])

        srv_record_layer.client = False

        srv_record_layer.version = (3,3)

        for result in srv_record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.handshake)
        self.assertEqual(parser.get(1), HandshakeType.client_hello)
        srv_record_layer._handshakeHashes.update(parser.bytes)
        srv_client_hello = ClientHello(head.ssl2).parse(parser)

        self.assertEqual(ClientHello, type(srv_client_hello))

        srv_cipher_suite = CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA
        srv_session_id = bytearray(0)

        srv_server_hello = ServerHello().create(
                (3,3), bytearray(32), srv_session_id, srv_cipher_suite,
                CertificateType.x509, None, None)

        srv_msgs = []
        srv_msgs.append(srv_server_hello)
        srv_msgs.append(Certificate(CertificateType.x509).
                create(srv_cert_chain))
        srv_msgs.append(ServerHelloDone())
        for msg in srv_msgs:
            srv_record_layer._handshakeHashes.update(msg.write())
        for result in srv_record_layer.sendMessages(srv_msgs):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break
        srv_record_layer._versionCheck = True

        #
        # client part
        #

        for result in record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.handshake)
        self.assertEqual(parser.get(1), HandshakeType.server_hello)
        record_layer._handshakeHashes.update(parser.bytes)
        server_hello = ServerHello().parse(parser)

        self.assertEqual(ServerHello, type(server_hello))

        for result in record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.handshake)
        self.assertEqual(parser.get(1), HandshakeType.certificate)
        record_layer._handshakeHashes.update(parser.bytes)
        server_certificate = Certificate(CertificateType.x509).parse(parser)
        self.assertEqual(Certificate, type(server_certificate))

        for result in record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.handshake)
        self.assertEqual(parser.get(1), HandshakeType.server_hello_done)
        record_layer._handshakeHashes.update(parser.bytes)
        server_hello_done = ServerHelloDone().parse(parser)
        self.assertEqual(ServerHelloDone, type(server_hello_done))

        public_key = server_certificate.certChain.getEndEntityPublicKey()

        premasterSecret = bytearray(48)
        premasterSecret[0] = 3 # 'cause we negotiatied TLSv1.2
        premasterSecret[1] = 3

        encryptedPreMasterSecret = public_key.encrypt(premasterSecret)

        client_key_exchange = ClientKeyExchange(
                CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA,
                (3,3))
        client_key_exchange.createRSA(encryptedPreMasterSecret)

        record_layer._handshakeHashes.update(client_key_exchange.write())
        for result in record_layer.sendMessage(client_key_exchange):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        master_secret = calcMasterSecret((3,3), premasterSecret,
                client_hello.random, server_hello.random)

        record_layer.calcPendingStates(
                CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA,
                master_secret, client_hello.random, server_hello.random,
                None)

        for result in record_layer.sendMessage(ChangeCipherSpec()):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        record_layer.changeWriteState()

        handshake_hashes = record_layer._handshakeHashes.digest((3, 3))
        verify_data = PRF_1_2(master_secret, b'client finished',
                handshake_hashes, 12)

        finished = Finished((3,3)).create(verify_data)
        record_layer._handshakeHashes.update(finished.write())
        for result in record_layer.sendMessage(finished):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        #
        # server part
        #

        for result in srv_record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.handshake)
        self.assertEqual(parser.get(1), HandshakeType.client_key_exchange)
        srv_record_layer._handshakeHashes.update(parser.bytes)

        srv_client_key_exchange = ClientKeyExchange(srv_cipher_suite,
                srv_record_layer.version).parse(parser)

        srv_premaster_secret = srv_private_key.decrypt(
                srv_client_key_exchange.encryptedPreMasterSecret)

        self.assertEqual(bytearray(b'\x03\x03' + b'\x00'*46),
                srv_premaster_secret)

        srv_master_secret = calcMasterSecret(srv_record_layer.version,
                srv_premaster_secret, srv_client_hello.random,
                srv_server_hello.random)

        srv_record_layer.calcPendingStates(srv_cipher_suite,
                srv_master_secret, srv_client_hello.random,
                srv_server_hello.random, None)

        for result in srv_record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("Blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.change_cipher_spec)
        srv_change_cipher_spec = ChangeCipherSpec().parse(parser)
        self.assertEqual(ChangeCipherSpec, type(srv_change_cipher_spec))

        srv_record_layer.changeReadState()

        srv_handshakeHashes = srv_record_layer._handshakeHashes.digest((3, 3))
        srv_verify_data = PRF_1_2(srv_master_secret, b"client finished",
                srv_handshakeHashes, 12)

        for result in srv_record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.handshake)
        self.assertEqual(parser.get(1), HandshakeType.finished)
        srv_record_layer._handshakeHashes.update(parser.bytes)
        srv_finished = Finished(srv_record_layer.version).parse(parser)
        self.assertEqual(Finished, type(srv_finished))
        self.assertEqual(srv_verify_data, srv_finished.verify_data)

        for result in srv_record_layer.sendMessage(ChangeCipherSpec()):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        srv_record_layer.changeWriteState()

        srv_handshakeHashes = srv_record_layer._handshakeHashes.digest((3, 3))
        srv_verify_data = PRF_1_2(srv_master_secret, b"server finished",
                srv_handshakeHashes, 12)

        srv_server_finished = Finished((3, 3)).create(srv_verify_data)
        srv_record_layer._handshakeHashes.update(srv_server_finished.write())
        for result in srv_record_layer.sendMessage(srv_server_finished):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        srv_record_layer.closed = False

        #
        # client part
        #

        for result in record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.change_cipher_spec)
        change_cipher_spec = ChangeCipherSpec().parse(parser)
        self.assertEqual(ChangeCipherSpec, type(change_cipher_spec))

        record_layer.changeReadState()

        handshake_hashes = record_layer._handshakeHashes.digest((3, 3))
        server_verify_data = PRF_1_2(master_secret, b'server finished',
                handshake_hashes, 12)

        for result in record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result

        self.assertEqual(head.type, ContentType.handshake)
        self.assertEqual(parser.get(1), HandshakeType.finished)
        server_finished = Finished(record_layer.version).parse(parser)

        self.assertEqual(Finished, type(server_finished))
        self.assertEqual(server_verify_data, server_finished.verify_data)

        record_layer.closed = False

        # try sending data
        for result in record_layer.sendMessage(ApplicationData().create(\
                bytearray(b'text\n'))):
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break

        # try recieving data
        for result in srv_record_layer.recvMessage():
            if result in (0, 1):
                raise Exception("blocking socket")
            else:
                break
        head, parser = result
        self.assertEqual(head.type, ContentType.application_data)
        data = ApplicationData().parse(parser).write()

        self.assertEqual(data, bytearray(b'text\n'))

        record_layer._shutdown(True)
        srv_record_layer._shutdown(True)

    @unittest.skip("needs external TLS server")
    def test_full_connection_with_external_server(self):

        # TODO test is slow (100ms) move to integration test suite
        #
        # start a regular TLS server locally before running this test
        # e.g.: openssl s_server -key localhost.key -cert localhost.crt

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 4433))

        record_layer = TLSRecordLayer(sock)

        record_layer.client = True
        record_layer.version = (3,3)

        client_hello = ClientHello()
        client_hello = client_hello.create((3,3), bytearray(32),
                bytearray(0), [CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA],
                None, None, False, False, None)

        for result in record_layer.sendMessage(client_hello):
            if result in (0,1):
                raise Exception("blocking socket")

        for result in record_layer._getMsg(ContentType.handshake,
                HandshakeType.server_hello):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        server_hello = result
        self.assertEqual(ServerHello, type(server_hello))

        for result in record_layer._getMsg(ContentType.handshake,
                HandshakeType.certificate, CertificateType.x509):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        server_certificate = result
        self.assertEqual(Certificate, type(server_certificate))

        for result in record_layer._getMsg(ContentType.handshake,
                HandshakeType.server_hello_done):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        server_hello_done = result
        self.assertEqual(ServerHelloDone, type(server_hello_done))

        public_key = server_certificate.certChain.getEndEntityPublicKey()

        premasterSecret = bytearray(48)
        premasterSecret[0] = 3 # 'cause we negotiatied TLSv1.2
        premasterSecret[1] = 3

        encryptedPreMasterSecret = public_key.encrypt(premasterSecret)

        client_key_exchange = ClientKeyExchange(
                CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA,
                (3,3))
        client_key_exchange.createRSA(encryptedPreMasterSecret)

        for result in record_layer.sendMessage(client_key_exchange):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        master_secret = calcMasterSecret((3,3), premasterSecret,
                client_hello.random, server_hello.random)

        record_layer.calcPendingStates(
                CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA,
                master_secret, client_hello.random, server_hello.random,
                None)

        for result in record_layer.sendMessage(ChangeCipherSpec()):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        record_layer.changeWriteState()

        handshake_hashes = record_layer._handshake_sha256.digest()
        verify_data = PRF_1_2(master_secret, b'client finished',
                handshake_hashes, 12)

        finished = Finished((3,3)).create(verify_data)
        for result in record_layer.sendMessage(finished):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        for result in record_layer._getMsg(ContentType.change_cipher_spec):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        change_cipher_spec = result
        self.assertEqual(ChangeCipherSpec, type(change_cipher_spec))

        record_layer.changeReadState()

        handshake_hashes = record_layer._handshake_sha256.digest()
        server_verify_data = PRF_1_2(master_secret, b'server finished',
                handshake_hashes, 12)

        for result in record_layer._getMsg(ContentType.handshake,
                HandshakeType.finished):
            if result in (0,1):
                raise Exception("blocking socket")
            else:
                break

        server_finished = result
        self.assertEqual(Finished, type(server_finished))
        self.assertEqual(server_verify_data, server_finished.verify_data)

        record_layer.closed = False

        record_layer.write(bytearray(b'text\n'))

        record_layer.close()

    def test_recvMessage(self):

        mock_sock = MockSocket(bytearray(
            b'\x16' +           # handshake
            b'\x03\x03' +       # TLSv1.2
            b'\x00\x04' +       # length
            b'\x0e' +           # server hello done
            b'\x00\x00\x00'     # length
            ))

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer.recvMessage():
            if result in (0,1):
                raise Exception("blocking socket")
            else: break

        header, p = result

        self.assertIsInstance(header, RecordHeader3)
        self.assertEqual(ContentType.handshake, header.type)
        self.assertEqual((3,3), header.version)
        self.assertIsInstance(p, Parser)
        self.assertEqual(bytearray(b'\x0e\x00\x00\x00'), p.bytes)

    def test_recvMessage_with_empty_handshake(self):

        mock_sock = MockSocket(bytearray(
            b'\x16' +           # handshake
            b'\x03\x03' +       # TLSv1.2
            b'\x00\x00'         # length
            ))

        record_layer = TLSRecordLayer(mock_sock)

        # empty handshake messages are disallowed by standard
        with self.assertRaises(TLSLocalAlert):
            for result in record_layer.recvMessage():
                if result in (0,1):
                    raise Exception("blocking socket")
                else:
                    break

    def test_recvMessage_with_multiple_messages_in_single_record(self):

        mock_sock = MockSocket(bytearray(
            b'\x16' +           # handshake
            b'\x03\x03' +       # TLSv1.2
            b'\x00\x35' +       # length
            # server hello
            b'\x02' +           # type - server hello
            b'\x00\x00\x26' +   # length
            b'\x03\x03' +       # TLSv1.2
            b'\x01'*32 +        # random
            b'\x00' +           # session ID length
            b'\x00\x2f' +       # cipher suite selected
            b'\x00' +           # compression method
            # certificate
            b'\x0b' +           # type - certificate
            b'\x00\x00\x03'     # length
            b'\x00\x00\x00'     # length of certificates
            # server hello done
            b'\x0e' +           # type - server hello done
            b'\x00\x00\x00'     # length
            ))

        record_layer = TLSRecordLayer(mock_sock)

        results = []
        for result in record_layer.recvMessage():
            if result in (0,1):
                raise Exception("blocking")
            else:
                results.append(result)
                if len(results) == 3:
                    break

        header, p = results[0]

        self.assertIsInstance(header, RecordHeader3)
        self.assertEqual(ContentType.handshake, header.type)
        self.assertEqual(42, len(p.bytes))
        self.assertEqual(HandshakeType.server_hello, p.bytes[0])

        header, p = results[1]

        self.assertIsInstance(header, RecordHeader3)
        self.assertEqual(ContentType.handshake, header.type)
        self.assertEqual(7, len(p.bytes))
        self.assertEqual(HandshakeType.certificate, p.bytes[0])

        header, p = results[2]

        self.assertIsInstance(header, RecordHeader3)
        self.assertEqual(ContentType.handshake, header.type)
        self.assertEqual(4, len(p.bytes))
        self.assertEqual(HandshakeType.server_hello_done, p.bytes[0])

    def test__sockRecvAll(self):

        mock_sock = MockSocket(bytearray(8), maxRet=1)

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer._sockRecvAll(4):
            if result in (0,1):
                raise Exception("blocking socket")
            else: break

        self.assertEqual(4, len(result))

        for result in record_layer._sockRecvAll(4):
            if result in (0,1):
                raise Exception("blocking socket")
            else: break

        self.assertEqual(4, len(result))

        for result in record_layer._sockRecvAll(1):
            break

        self.assertEqual(0, result)

    def test__sockRecvAll_with_empty_read(self):
        mock_sock = MockSocket(bytearray(4))

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer._sockRecvAll(0):
            if result in (0,1):
                raise Exception("blocking socket")
            else: break

        self.assertEqual(bytearray(0), result)

    def test__sockRecvRecord_with_SSL3_record(self):
        mock_sock = MockSocket(bytearray(
            b'\x16' +           # handshake protocol
            b'\x03\x03' +       # version
            b'\x00\x02' +       # length
            b'\x00\x00'
            ))

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer._sockRecvRecord():
            if result in (0,1):
                raise Exception("blocking socket")
            else: break

        r, b = result

        self.assertEqual(ContentType.handshake, r.type)
        self.assertEqual((3,3), r.version)
        self.assertEqual(2, r.length)
        self.assertFalse(r.ssl2)
        self.assertEqual(bytearray(2), b)

    def test__sockRecvRecord_with_empty_SSL3_record(self):
        mock_sock = MockSocket(bytearray(
            b'\x16' +           # handshake protocol
            b'\x03\x03' +       # version
            b'\x00\x00'         # length
            ))

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer._sockRecvRecord():
            if result in (0,1):
                raise Exception("blocking socket")
            else: break

        r, b = result

        self.assertEqual(ContentType.handshake, r.type)
        self.assertEqual((3,3), r.version)
        self.assertEqual(0, r.length)
        self.assertFalse(r.ssl2)
        self.assertEqual(bytearray(0), b)

    def test__sockRecvRecord_with_non_complete_SSL3_record(self):
        mock_sock = MockSocket(bytearray(
            b'\x16' +           # handshake protocol
            b'\x03\x03' +       # version
            b'\x00\x04' +       # length
            b'\x00\x00'         # just 2 out of 4 bytes of data
            ))

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer._sockRecvRecord():
            break

        self.assertEqual(0, result)

    def test__sockRecvRecord_with_SSL2_record(self):
        mock_sock = MockSocket(bytearray(
            b'\x80' +           # header
            b'\x04' +           # length
            b'\x00'*4           # data
            ))

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer._sockRecvRecord():
            if result in (0,1):
                raise Exception("blocking socket")
            else: break

        r, b = result

        self.assertEqual(ContentType.handshake, r.type)
        self.assertEqual((2,0), r.version)
        self.assertEqual(4, r.length)
        self.assertTrue(r.ssl2)
        self.assertEqual(bytearray(4), b)

    def test__sockRecvRecord_with_empty_SSL2_record(self):
        mock_sock = MockSocket(bytearray(
            b'\x80' +           # header
            b'\x00'             # length
            ))

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer._sockRecvRecord():
            if result in (0,1):
                raise Exception("blocking socket")
            else: break

        r, b = result

        self.assertEqual(ContentType.handshake, r.type)
        self.assertEqual((2,0), r.version)
        self.assertEqual(0, r.length)
        self.assertTrue(r.ssl2)
        self.assertEqual(bytearray(0), b)

    def test__sockRecvRecord_with_non_complete_SSL2_record(self):
        mock_sock = MockSocket(bytearray(
            b'\x80'            # header
            ))

        record_layer = TLSRecordLayer(mock_sock)

        for result in record_layer._sockRecvRecord():
            break

        self.assertEqual(0, result)
