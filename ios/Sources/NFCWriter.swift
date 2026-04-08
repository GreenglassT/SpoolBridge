import Foundation
import CoreNFC

class NFCWriter: NSObject, ObservableObject, NFCNDEFReaderSessionDelegate {
    @Published var status: WriteStatus = .idle

    enum WriteStatus: Equatable {
        case idle
        case waiting
        case writing
        case success(String)  // tag UID
        case error(String)
    }

    private var session: NFCNDEFReaderSession?
    private var jsonData: Data?
    private var urlString: String?
    private var onComplete: ((String?) -> Void)?

    func write(jsonData: Data, url: String? = nil, onComplete: ((String?) -> Void)? = nil) {
        self.jsonData = jsonData
        self.urlString = url
        self.onComplete = onComplete
        self.status = .waiting

        session = NFCNDEFReaderSession(delegate: self, queue: nil, invalidateAfterFirstRead: false)
        session?.alertMessage = "Hold your iPhone near the NFC tag to write."
        session?.begin()
    }

    // MARK: - NFCNDEFReaderSessionDelegate

    func readerSessionDidBecomeActive(_ session: NFCNDEFReaderSession) {}

    func readerSession(_ session: NFCNDEFReaderSession, didInvalidateWithError error: Error) {
        DispatchQueue.main.async {
            let nfcError = error as? NFCReaderError
            if nfcError?.code == .readerSessionInvalidationErrorUserCanceled {
                self.status = .idle
            } else if self.status != .success("") && !"\(self.status)".hasPrefix("success") {
                self.status = .error(error.localizedDescription)
            }
            self.onComplete?(nil)
        }
    }

    func readerSession(_ session: NFCNDEFReaderSession, didDetectNDEFs messages: [NFCNDEFMessage]) {}

    func readerSession(_ session: NFCNDEFReaderSession, didDetect tags: [NFCNDEFTag]) {
        guard let tag = tags.first, let jsonData else {
            session.invalidate(errorMessage: "No tag or data.")
            return
        }

        DispatchQueue.main.async { self.status = .writing }

        session.connect(to: tag) { error in
            if let error {
                session.invalidate(errorMessage: "Connection failed: \(error.localizedDescription)")
                return
            }

            tag.queryNDEFStatus { status, _, error in
                guard status == .readWrite else {
                    session.invalidate(errorMessage: "Tag is not writable.")
                    return
                }

                // Build NDEF records
                var records: [NFCNDEFPayload] = []

                // 1. OpenSpool JSON as application/json MIME record
                let jsonPayload = NFCNDEFPayload(
                    format: .media,
                    type: "application/json".data(using: .utf8)!,
                    identifier: Data(),
                    payload: jsonData
                )
                records.append(jsonPayload)

                // 2. Optional URL record
                if let urlString = self.urlString,
                   let urlPayload = NFCNDEFPayload.wellKnownTypeURIPayload(string: urlString) {
                    records.append(urlPayload)
                }

                let message = NFCNDEFMessage(records: records)

                tag.writeNDEF(message) { error in
                    if let error {
                        session.invalidate(errorMessage: "Write failed: \(error.localizedDescription)")
                        return
                    }

                    // Read back the tag identifier
                    var tagUID = ""
                    if let nfcTag = tag as? NFCMiFareTag {
                        tagUID = nfcTag.identifier.map { String(format: "%02X", $0) }.joined()
                    }

                    session.alertMessage = "Tag written!"
                    session.invalidate()

                    DispatchQueue.main.async {
                        self.status = .success(tagUID)
                        self.onComplete?(tagUID.isEmpty ? nil : tagUID)
                    }
                }
            }
        }
    }
}
